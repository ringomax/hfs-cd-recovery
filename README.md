# hfs-cd-recovery

Recover files from classic Mac **HFS Standard** CD-ROMs that modern macOS refuses to mount.

```
The disk you attached was not readable by this computer.
```

If you see this after inserting an old Mac CD, the disc is probably fine. macOS
removed the HFS Standard read driver in **10.15 Catalina**, so nothing on a
current Mac can interpret the volume.

Tested on macOS 26.5.2, Apple M4 Max, with an Apple USB SuperDrive, against two
CD-ROMs mastered in 2007 and 2013. 8,806 files recovered.

---

## The part that matters: read `/dev/rdiskN`, not `/dev/diskN`

**This is tool-independent.** Use `dd`, `ddrescue`, or the scripts here — if you
point any of them at the buffered block device, you get silently corrupt data.

A CD sector is 2352 bytes. A memory page is 4096. They do not divide evenly, and
reading an optical drive through `/dev/diskN` returns **duplicated sector runs**
— one region of the disc overwriting another, in 4096- and 8192-byte periods.

No read errors. Exit code 0. The image is simply wrong.

```
164124+0 records in
164124+0 records out
386019648 bytes transferred in 173.616624 secs
```

That run silently replaced 264 of 1593 catalog leaf nodes with copies of their
neighbours, and 786 files disappeared from the listing. Every surface indicator
said the rip was fine, including the extracted images opening normally.

| read path | duplicated leaf nodes |
| --- | --- |
| `dd if=/dev/disk62 bs=2352` | 264 |
| `dd if=/dev/disk62 bs=4816896` | 333 |
| `hdiutil create -srcdevice /dev/disk62` | **0** |
| `dd if=/dev/rdisk62 bs=4816896` | **0** |

The raw character device output is byte-identical to `hdiutil`'s.

> The mechanism is inferred from the alignment: the corruption period is always a
> multiple of the page size, and only the cached path is affected. Confirming it
> in the kernel is left as an exercise.

---

## Should you use these scripts?

Probably only `verify.py`. Be honest about what already exists:

| task | mature tool | what's here |
| --- | --- | --- |
| rip a failing disc, resumably | **[GNU ddrescue](https://www.gnu.org/software/ddrescue/)** | `chunkrip.sh` — a smaller version of the same idea |
| extract files from an HFS image | **[HFSExplorer](https://www.catacombae.org/hfsexplorer/)**, [`hfsutils`](https://www.mars.org/home/rob/proj/hfs/) | `ripcd.py` — no Java, no install |
| prove the rip is not silently corrupt | — | `verify.py` |

`ddrescue` has a mapfile for resume, `-R` for reverse passes, `-b` for sector
size, and adaptive bad-area splitting that `chunkrip.sh` does not attempt. If
you can install it, prefer it:

```bash
brew install ddrescue
ddrescue -b 2352 -r3    /dev/rdisk62 disc.raw disc.map
ddrescue -b 2352 -r3 -R /dev/rdisk62 disc.raw disc.map   # reverse pass
```

*Not yet verified against a real disc — the scripts here were used for the
actual recovery. Reports welcome.*

HFSExplorer reads HFS, HFS+ and HFSX, handles raw images and Apple Partition
Map natively, and predates this by many years. `ripcd.py` exists because it
needs nothing but Python 3 and because it reports catalog counts, which is what
made the corruption visible in the first place.

**What is not already covered is verification.** That is the one thing worth
taking from this repo.

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

`verify.py` accepts any correctly-sectored image, including one produced by
`ddrescue` or `hdiutil`, so you can use it as a check on someone else's rip.

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

Resumable raw ripper in 1.2 MB chunks, recording which ones succeeded.

Bus-powered drives such as the Apple USB SuperDrive draw more current when they
retry over a damaged region, and can drop off the USB bus entirely — the device
disappears from `ioreg`, not just the read fails. `hdiutil` handles that badly:
it works as one long transaction and deletes its output on failure, so a dropout
at 95% costs you everything.

`reverse` reads the remaining chunks from the end backwards, which matters when
the drive dies at a fixed spot — the outer edge of a disc is where scratches and
dirt concentrate. Chunks that fail while the drive is still alive are recorded
as bad and skipped on later runs.

Again: `ddrescue` does all of this and more. This exists for the case where you
want zero dependencies.

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
- Prints catalog counts against the header's own counts on every run.

### `verify.py`

The three checks above.

---

## Requirements

- macOS with an optical drive
- Python 3.8+, no third-party packages

## Limitations

- HFS Standard only. HFS Plus volumes mount natively on macOS.
- Mode 1 CD sectors, 2352 bytes with a 2048-byte payload. Mode 2 Form 1 discs
  need a different offset.
- Physically unreadable regions cannot be recovered. Cleaning the disc from the
  centre outwards is worth trying first.

## A note on tool failures

`hmount` failing with `malformed b*-tree header node` usually means the image is
corrupt, not that the tool is too old. That error is what a duplicated-sector
read looks like from the outside — it cost a day of chasing the wrong cause.
Given a correctly read image, `hfsutils` mounts and lists these volumes fine.

7-Zip does not help here — its HFS handler expects HFS Plus.

## License

MIT
