# hfs-cd-recovery

Recover files from classic Mac **HFS Standard** CD-ROMs that modern macOS refuses to mount.

```
The disk you attached was not readable by this computer.
```

If you see this after inserting an old Mac CD, the disc is probably fine. macOS
removed the HFS Standard read driver in **10.15 Catalina**, so nothing on a
current Mac can interpret the volume. These scripts read the disc at the block
level and walk the HFS catalog directly.

Tested on macOS 26.5.2, Apple M4 Max, with an Apple USB SuperDrive, against two
CD-ROMs mastered in 2007 and 2013.

---

## Read `/dev/rdiskN`, not `/dev/diskN`

This is the part that will cost you a day if you get it wrong.

A CD sector is 2352 bytes. A memory page is 4096. They do not divide evenly, and
reading an optical drive through the **buffered** block device returns
**duplicated sector runs** — one region of the disc overwriting another, in
4096- and 8192-byte periods.

`dd` reports success. There are no read errors. The image is simply wrong.

```
164124+0 records in
164124+0 records out
386019648 bytes transferred in 173.616624 secs
```

That run silently replaced 264 of 1593 catalog leaf nodes with copies of their
neighbours, and 786 files disappeared from the listing.

| read path | duplicated leaf nodes |
| --- | --- |
| `dd if=/dev/disk62 bs=2352` | 264 |
| `dd if=/dev/disk62 bs=4816896` | 333 |
| `hdiutil create -srcdevice /dev/disk62` | **0** |
| `dd if=/dev/rdisk62 bs=4816896` | **0** |

The raw character device output is byte-identical to `hdiutil`'s. Use it.

> The mechanism is inferred from the alignment: the corruption period is always a
> multiple of the page size, and only the cached path is affected. Confirming it
> in the kernel is left as an exercise.

---

## Quick start

```bash
# 1. Find the drive and the sector count
drutil status
DEV=/dev/rdisk62
TOTAL=$(drutil status | awk '/blocks:/{print $3}')

# 2. Rip. Resumable — re-run after a drive dropout and it continues
./chunkrip.sh $DEV disc.raw $TOTAL

# 3. If the drive keeps dying at a fixed spot, secure the rest first
./chunkrip.sh $DEV disc.raw $TOTAL reverse

# 4. Extract
./ripcd.py disc.raw ./out

# 5. Prove the rip was good before you trust it
./verify.py disc.raw --extracted ./out
```

`verify.py` output on a good rip:

```
volume 'IRYO'
  [ok] counts: catalog 4657 files / 121 dirs, header says 4657 / 121
  [ok] catalog: 1951 leaf nodes, 0 duplicated
  [ok] extracted: 4002 valid, 0 broken, 127 not checked
PASS
```

---

## Why verification matters

A bad read does not announce itself, so "the rip finished" proves nothing.
`verify.py` runs three independent checks:

1. **Counts** — the catalog is walked and compared against `drFilCnt` and
   `drDirCnt` in the volume header. These are written at mastering time and are
   not derived from the catalog, so they work as a checksum the format hands you
   for free.
2. **Duplicate leaf nodes** — every catalog leaf node is hashed. A healthy
   B-tree has no byte-identical pairs. Any hit means the read substituted one
   region of the disc for another.
3. **File signatures** — magic bytes and end markers of every extracted JPEG,
   PNG, GIF, TIFF and PDF.

All three have to pass.

---

## Tools

### `chunkrip.sh`

Resumable raw ripper. Reads in 1.2 MB chunks and records which ones succeeded.

Bus-powered drives such as the Apple USB SuperDrive draw more current when they
retry over a damaged region, and can drop off the USB bus entirely — the device
disappears from `ioreg`, not just the read fails. `hdiutil` handles that badly:
it works as one long transaction and deletes its output on failure, so a dropout
at 95% costs you everything.

This script keeps every chunk it got. Reconnect the drive, run it again, and it
picks up where it stopped. `reverse` reads the remaining chunks from the end
backwards, which matters when the drive dies at a fixed spot — the outer edge of
a disc is where scratches and dirt concentrate. Chunks that fail while the drive
is still alive are recorded as bad and skipped on later runs.

### `ripcd.py`

Parses the Apple Partition Map, locates the HFS Standard volume, walks the
catalog B-tree, and writes every file out with its original path.

- File names are decoded as Shift-JIS with a Mac Roman fallback, so Japanese
  discs come out with readable names.
- Extents overflow is handled, for files too fragmented for the three extents in
  the catalog record.
- If the primary volume header is unreadable, it falls back to the **alternate
  MDB** that HFS keeps in the second-to-last block of the volume. That
  redundancy is from 1985 and it still works.
- Resource forks are written as `<name>.rsrc`, but only for files whose data
  fork is empty.

### `verify.py`

The three checks above.

---

## Requirements

- macOS with an optical drive
- Python 3.8+, no third-party packages

---

## Limitations

- HFS Standard only. HFS Plus volumes mount natively on macOS, so this is not
  needed for them.
- Mode 1 CD sectors, 2352 bytes with a 2048-byte payload. Mode 2 Form 1 discs
  need a different offset.
- Physically unreadable regions cannot be recovered. Cleaning the disc from the
  centre outwards is worth trying first.

## Alternatives

Once you have a **correctly read** image, [`hfsutils`](https://www.mars.org/home/rob/proj/hfs/)
can mount and copy from it:

```bash
brew install hfsutils
hmount volume.hfs
hls
```

It is worth knowing that `hmount` failing with `malformed b*-tree header node`
usually means the image is corrupt, not that the tool is too old. That error is
what a duplicated-sector read looks like from the outside.

7-Zip does not help here — its HFS handler expects HFS Plus.

## License

MIT
