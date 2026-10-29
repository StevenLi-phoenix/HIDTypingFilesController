# pi-standalone — type the payload with no venv, no network

`~/main.py` on the Pi needs `tqdm` + `dotenv` from `~/.venv`, and driving it needs an
ssh session, which needs Wi-Fi. This folder removes both dependencies, so a Pi that
only has the USB cable and a full-ish SD card can still do the job.

| file | what it is |
|---|---|
| `main_lite.py` | same key map as `main.py`, stdlib only (`python3`, nothing to pip) |
| `go.sh` | waits for the host to enumerate the keyboard, counts down, then types |
| `check.sh` | read-only diagnose: UDC state, `df -h`, biggest dirs, log volume |
| `payload.txt` | the HID payload to type — your own, kept local (never committed) |

## Run

```sh
# drop the .txt produced by hid_pack.py here as payload.txt (local only, do not commit)
python3 main_lite.py payload.txt --dry-run   # verify chars only, no writes
sudo sh go.sh                                # wait for link, 20 s countdown, type
sudo sh go.sh payload.txt 30                 # longer countdown
```

`go.sh` prints `udc: not attached` → `udc: configured` when the target machine
enumerates the keyboard, then counts down so you can focus the target editor.
No host on the other end means no writes at all, so it cannot hang like `main.py` did.

## Getting these onto the Pi, worst case first

1. Wi-Fi works: `scp -r pi-standalone hid:~/`
2. No network: copy to the FAT boot partition (`/Volumes/bootfs`) on the Mac, then on the
   Pi `sudo mount /boot; cp /boot/pi-standalone/* ~/` (the boot partition is FAT, so
   `chmod` does not survive — use `sh go.sh` / `python3 main_lite.py`, not `./`).
3. No shell at all: the Pi only needs `main_lite.py` + your payload; copy both to the
   boot partition and type from there (~40 KB with a typical payload).

## If the SD card is full

`check.sh` shows `df -h /`; 100 % on `/` explains a Pi that powers up but never joins
Wi-Fi (wpa_supplicant cannot write its state) and a `main.py` that dies oddly.

```sh
sudo journalctl --vacuum-size=50M
sudo rm -f /var/log/*.gz /var/log/*.1
rm -f ~/nohup.out ~/*.patch
```

A card that is full enough can also fail to boot: green LED solid, no SD activity.
Pull the card, mount it on the Mac, and free space offline — or keep a freshly flashed
card as the fallback, since none of this needs the old `.venv` anymore.
