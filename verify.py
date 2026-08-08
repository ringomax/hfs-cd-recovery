#!/usr/bin/env python3
"""Integrity checks for a ripped HFS Standard CD image.

A bad read of an optical drive does not necessarily produce read errors. On
macOS, reading through /dev/diskN instead of /dev/rdiskN returns duplicated
sector runs while dd reports success, so "the rip finished" proves nothing.

Three independent checks catch it:

  1. catalog file/dir counts vs. the counts the volume header declares
  2. byte-identical duplicate leaf nodes in the catalog B-tree
  3. magic bytes and end markers of every extracted file

Usage:
  ./verify.py <image.raw>                  # checks 1 and 2
  ./verify.py <image.raw> --extracted DIR  # also check 3
"""
import hashlib
import os
import struct
import sys
import zlib
from collections import Counter

from ripcd import NODE, Volume, partitions, user_data


def duplicate_leaf_nodes(vol):
    """Count leaf nodes that are byte-identical to another leaf node.

    A healthy B-tree has none. Any hit means the read substituted one region
    of the disc for another.
    """
    seen = Counter()
    leaves = 0
    size, ext = vol.ct
    for start, count in ext:
        if not count:
            continue
        base = vol.ablk(start)
        for i in range(count * vol.al_siz // NODE):
            node = vol.img[base + i * NODE: base + (i + 1) * NODE]
            if len(node) == NODE and node[8] == 0xFF:
                leaves += 1
                seen[hashlib.md5(node).hexdigest()] += 1
    return leaves, sum(v - 1 for v in seen.values() if v > 1)


def png_ok(b):
    """Validate every PNG chunk's CRC-32, not just the header and IEND.

    Head-and-tail checks pass on a file whose middle was replaced by a bad read:
    the magic bytes and the end marker survive, only the pixels are wrong. PNG
    carries a CRC per chunk, so it can be checked properly. That is what made it
    possible to tell two rips of the same disc apart.
    """
    if b[:8] != b'\x89PNG\r\n\x1a\n':
        return False
    i = 8
    while i + 12 <= len(b):
        length = struct.unpack_from('>I', b, i)[0]
        kind = b[i + 4:i + 8]
        if i + 12 + length > len(b):
            return False
        stored = struct.unpack_from('>I', b, i + 8 + length)[0]
        if zlib.crc32(kind + b[i + 8:i + 8 + length]) & 0xFFFFFFFF != stored:
            return False
        i += 12 + length
        if kind == b'IEND':
            return True
    return False


SIGNATURES = {
    ('.jpg', '.jpeg'): (lambda b: b[:2] == b'\xff\xd8' and b.rstrip(b'\x00')[-2:] == b'\xff\xd9'),
    ('.png',): png_ok,
    ('.gif',): (lambda b: b[:6] in (b'GIF87a', b'GIF89a') and b.rstrip(b'\x00')[-1:] == b';'),
    ('.tif', '.tiff'): (lambda b: b[:4] in (b'II*\x00', b'MM\x00*')),
    ('.pdf',): (lambda b: b[:5] == b'%PDF-' and b'%%EOF' in b[-1024:]),
}


def check_extracted(root):
    ok = bad = skipped = 0
    broken = []
    for dirpath, _, names in os.walk(root):
        for name in names:
            path = os.path.join(dirpath, name)
            low = name.lower()
            test = next((fn for exts, fn in SIGNATURES.items()
                         if low.endswith(exts)), None)
            if test is None:
                skipped += 1
                continue
            with open(path, 'rb') as fh:
                data = fh.read()
            if test(data):
                ok += 1
            else:
                bad += 1
                broken.append((os.path.relpath(path, root), len(data)))
    return ok, bad, skipped, broken


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2

    img = user_data(sys.argv[1])
    vols = []
    for typ, off, size in partitions(img):
        if typ != 'Apple_HFS':
            continue
        if img[off + 1024:off + 1026] == b'BD':
            vols.append(Volume(img, off))
        elif img[off + size - 1024:off + size - 1022] == b'BD':
            vols.append(Volume(img, off, off + size - 1024))

    if not vols:
        print('no readable HFS Standard volume found')
        return 1

    failed = False
    for vol in vols:
        print(f'volume {vol.name!r}')

        files, dirs = len(vol.files), len(vol.dirs) - 1
        match = files == vol.n_files and dirs == vol.n_dirs
        print(f'  [{"ok" if match else "FAIL"}] counts: '
              f'catalog {files} files / {dirs} dirs, '
              f'header says {vol.n_files} / {vol.n_dirs}')
        failed |= not match

        leaves, dups = duplicate_leaf_nodes(vol)
        print(f'  [{"ok" if dups == 0 else "FAIL"}] catalog: '
              f'{leaves} leaf nodes, {dups} duplicated')
        failed |= dups != 0

    if '--extracted' in sys.argv:
        root = sys.argv[sys.argv.index('--extracted') + 1]
        ok, bad, skipped, broken = check_extracted(root)
        print(f'  [{"ok" if bad == 0 else "FAIL"}] extracted: '
              f'{ok} valid, {bad} broken, {skipped} not checked')
        for path, size in broken[:10]:
            print(f'      {path} ({size} bytes)')
        if len(broken) > 10:
            print(f'      ... and {len(broken) - 10} more')
        failed |= bad != 0

    print('PASS' if not failed else 'FAIL — the read is not trustworthy')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
