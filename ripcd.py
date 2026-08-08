#!/usr/bin/env python3
"""Extract files from a classic Mac HFS Standard CD image.

  1) ./chunkrip.sh /dev/rdiskN disc.raw <total-sectors>
  2) ./ripcd.py disc.raw <output-dir>
  3) ./verify.py disc.raw --extracted <output-dir>

Step 1 must read /dev/rdiskN, the raw character device. Reading /dev/diskN
instead sends 2352-byte CD sectors through the buffer cache, which aliases them
against 4096-byte pages and returns duplicated sector runs. dd reports success
and the catalog comes out silently corrupt. Step 3 is what catches it.

Also accepts a .cdr from `hdiutil create -srcdevice`, which reads correctly.
"""
import os, struct, sys

SEC_RAW, SEC_USER, NODE = 2352, 2048, 512


def user_data(path):
    """Strip 12B sync + 4B header (and trailing ECC) from each raw Mode-1 sector."""
    raw = open(path, 'rb').read()
    if raw[:12] != b'\x00' + b'\xff' * 10 + b'\x00':
        return raw                                    # already cooked
    return b''.join(raw[i * SEC_RAW + 16:i * SEC_RAW + 16 + SEC_USER]
                    for i in range(len(raw) // SEC_RAW))


def decode(b):
    for enc in ('shift_jis', 'mac_roman'):
        try:
            return b.decode(enc)
        except UnicodeDecodeError:
            pass
    return b.decode('mac_roman', 'replace')


def leaf_records(vol, base, count):
    """Yield records from every leaf node in a B-tree occupying [base, base+count*NODE)."""
    for i in range(count):
        n = vol[base + i * NODE: base + (i + 1) * NODE]
        if len(n) < NODE or n[8] != 0xFF:
            continue
        nrec = struct.unpack_from('>H', n, 10)[0]
        if not 0 < nrec < 100:
            continue
        offs = [struct.unpack_from('>H', n, NODE - 2 * (j + 1))[0] for j in range(nrec + 1)]
        if not all(14 <= offs[j] < offs[j + 1] <= NODE - 2 * (nrec + 1) for j in range(nrec)):
            continue
        for j in range(nrec):
            yield n[offs[j]:offs[j + 1]]


class Volume:
    def __init__(self, img, start, mdb_off=None):
        self.img, self.start = img, start
        mdb = img[mdb_off: mdb_off + 512] if mdb_off is not None else \
            img[start + 1024: start + 1536]
        self.mdb = mdb
        self.name = decode(mdb[37:37 + mdb[36]])
        self.al_siz = struct.unpack_from('>I', mdb, 20)[0]
        self.al_st = struct.unpack_from('>H', mdb, 28)[0]
        self.n_files = struct.unpack_from('>I', mdb, 84)[0]
        self.n_dirs = struct.unpack_from('>I', mdb, 88)[0]
        self.xt = (struct.unpack_from('>I', mdb, 130)[0],
                   [struct.unpack_from('>HH', mdb, 134 + i * 4) for i in range(3)])
        self.ct = (struct.unpack_from('>I', mdb, 146)[0],
                   [struct.unpack_from('>HH', mdb, 150 + i * 4) for i in range(3)])
        self._read_extents_overflow()
        self._read_catalog()

    def ablk(self, n):
        return self.start + self.al_st * 512 + n * self.al_siz

    def _read_extents_overflow(self):
        self.overflow = {}
        size, ext = self.xt
        for st, cnt in ext:
            if not cnt:
                continue
            for r in leaf_records(self.img, self.ablk(st), cnt * self.al_siz // NODE):
                if len(r) < 19 or r[0] != 7:
                    continue
                fork, cnid, fabn = r[1], struct.unpack_from('>I', r, 2)[0], \
                    struct.unpack_from('>H', r, 6)[0]
                exts = [struct.unpack_from('>HH', r, 8 + i * 4) for i in range(3)]
                self.overflow.setdefault((cnid, fork), []).append((fabn, exts))

    def _read_catalog(self):
        self.dirs, self.files = {2: (0, '')}, {}
        size, ext = self.ct
        for st, cnt in ext:
            if not cnt:
                continue
            for r in leaf_records(self.img, self.ablk(st), cnt * self.al_siz // NODE):
                if len(r) < 8:
                    continue
                klen, parent, nlen = r[0], struct.unpack_from('>I', r, 2)[0], r[6]
                if not 6 <= klen <= 37 or 7 + nlen > len(r):
                    continue
                name = decode(r[7:7 + nlen])
                o = 1 + klen
                o += o & 1
                if o >= len(r):
                    continue
                if r[o] == 1 and o + 16 <= len(r):                       # directory
                    self.dirs[struct.unpack_from('>I', r, o + 6)[0]] = (parent, name)
                elif r[o] == 2 and o + 102 <= len(r):                    # file
                    self.files[struct.unpack_from('>I', r, o + 20)[0]] = dict(
                        parent=parent, name=name,
                        type=r[o + 4:o + 8].decode('mac_roman', 'replace'),
                        dlen=struct.unpack_from('>I', r, o + 26)[0],
                        rlen=struct.unpack_from('>I', r, o + 36)[0],
                        dext=[struct.unpack_from('>HH', r, o + 74 + i * 4) for i in range(3)],
                        rext=[struct.unpack_from('>HH', r, o + 86 + i * 4) for i in range(3)])

    def path(self, pid):
        parts, guard = [], 0
        while pid > 2 and pid in self.dirs and guard < 64:
            pid, nm = self.dirs[pid]
            parts.append(nm)
            guard += 1
        return '/'.join(reversed(parts))

    def fork(self, cnid, forktype, ext, length):
        chunks, have = [], 0
        for st, cnt in ext:
            if cnt:
                chunks.append(self.img[self.ablk(st): self.ablk(st) + cnt * self.al_siz])
                have += cnt
        # extents overflow entries continue the fork, in starting-block order
        for fabn, exts in sorted(self.overflow.get((cnid, forktype), [])):
            for st, cnt in exts:
                if cnt:
                    chunks.append(self.img[self.ablk(st): self.ablk(st) + cnt * self.al_siz])
        return b''.join(chunks)[:length]


def partitions(img):
    if img[0:2] != b'ER':
        return []
    out = []
    for i in range(1, 64):
        e = img[i * 512:(i + 1) * 512]
        if e[0:2] != b'PM':
            break
        st, sz = struct.unpack('>II', e[8:16])
        typ = e[48:80].split(b'\0')[0].decode('mac_roman', 'replace')
        out.append((typ, st * 512, sz * 512))
    return out


def main():
    img = user_data(sys.argv[1])
    dest = sys.argv[2]
    vols = []
    for typ, off, sz in partitions(img):
        if typ != 'Apple_HFS':
            continue
        if img[off + 1024:off + 1026] == b'BD':
            vols.append(Volume(img, off))
        elif img[off + sz - 1024:off + sz - 1022] == b'BD':
            # primary MDB unreadable -- fall back to the alternate MDB that HFS
            # keeps in the second-to-last block of the volume
            print(f"  primary MDB damaged at {off + 1024}; using alternate MDB")
            vols.append(Volume(img, off, off + sz - 1024))
    if not vols:
        vols = [Volume(img, o - 1024) for o in range(0, len(img) - 512, 512)
                if img[o:o + 2] == b'BD' and 512 <= struct.unpack_from('>I', img, o + 20)[0] <= 1 << 20][:1]
    for v in vols:
        print(f"volume {v.name!r}: catalog files={len(v.files)} dirs={len(v.dirs) - 1} "
              f"(MDB says files={v.n_files} dirs={v.n_dirs})")
        ok = short = 0
        for cnid, f in v.files.items():
            rel = os.path.join(v.path(f['parent']), f['name']).lstrip('/')
            tgt = os.path.join(dest, rel)
            os.makedirs(os.path.dirname(tgt) or dest, exist_ok=True)
            b = v.fork(cnid, 0, f['dext'], f['dlen'])
            open(tgt, 'wb').write(b)
            if len(b) == f['dlen']:
                ok += 1
            else:
                short += 1
                print(f"  SHORT {rel}: {len(b)}/{f['dlen']}")
            if f['dlen'] == 0 and f['rlen']:
                open(tgt + '.rsrc', 'wb').write(v.fork(cnid, 0xFF, f['rext'], f['rlen']))
        print(f"  extracted ok={ok} short={short} -> {dest}")


if __name__ == '__main__':
    main()
