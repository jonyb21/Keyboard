# Custom firmware on the AULA F75 Max: feasibility (2026-10-31)

Question: can Jon run his own firmware on the F75 Max that talks to his own app?

## Bottom line

Yes in principle, but not yet in practice. No open-source firmware runs on this board, so it would be a new port. The board has not been dumped, and there is no tested way to recover it after a bad flash.

Recommended path: keep the stock firmware and grow the app (Path A). Revisit custom firmware only after a verified full dump of the chip.

## What is known (observed)

- **The vendor updater bundles this board's firmware.**
  - Its name is `DP25223 ... V1.13_20261015_0xA7CE.exe` (sha256 `56a736ae…22c7`).
  - It is a SONiX USB MCU ISP Tool, an in-system flashing tool, built with MFC in VS2008.
  - It carries a 256 KB image tagged `0C45:800A` version 0x0113, in resource 4000.
  - It also carries a sibling `SN32F290.hex` tagged `0C45:8801`.
  - Its chip list includes SN32F290.
- **The board now reports firmware 0x0113.**
  - On 2026-10-31, `remap apply` of `board_map.json` (17 keys) was acknowledged with no error on 0x0113.
  - So the stock remap protocol still works after the update.
- **The F108 Pro is described as SN32F290 plus SPI flash for graphics.**
  - That is a sibling board on the same `800A` platform.
  - Source: `AuRoN89/AULA-F108-PRO-Reverse-Engineering`, a third-party README.
- **The SN32F290 has a Boot ROM and a bit that can disable SWD.**
  - SWD is the chip's debug and programming port.
  - Source: the `SN32F290.h` header in ChibiOS-Contrib.
- **SonixQMK does not support the SN32F290.** Its SWD dumper names only SN32F248, SN32F248B and VS11K09A.

## Unconfirmed

- **The chip.**
  - That this board uses an SN32F290 has not been read off the chip itself.
  - One unverified source lists a different MCU for an F75, the SH68F90A.
- **The flash path.** Which image the updater writes to `800A`, and how it enters the bootloader, have not been traced in code.
- **The image contents.**
  - Whether the image is plain, unencrypted code rests only on its entropy.
  - Nobody has disassembled it.
- **The option word.**
  - The option word at `0x3FFFC` may set code protection.
  - Never write to it.
- **The radio, the LCD controller and the internal links are unknown.**
  - It is not known whether 2.4G and Bluetooth run on a separate radio chip.
  - The dongle (`05AC:024F`) is a separate USB device.

## Paths

| Path | What it is | Effort | Brick risk | Trade-offs |
|---|---|---|---|---|
| A | Stock firmware, app drives everything over HID | Low to medium | None | Limited to what the stock protocol offers. Macros, Fn layer, key response, sleep and locks are already recovered from the vendor app and only need adding to the app. |
| B | Custom wired-only firmware (a new SN32F290 port in the style of SonixQMK) | High: weeks or more | Medium to high until there is a dump and a tested recovery | Full control of keys and the protocol. Wireless is lost, and the LCD/RGB until they are ported. |
| C | Full custom firmware including wireless and LCD | Very high | High | Depends on the radio and LCD unknowns above. |

## Safe first steps (no flashing)

1. Archive the updater and the extracted images with their hashes. Do not run the updater again on its own.
2. Trace statically which image the updater sends to `800A`, and how it enters ISP mode.
3. Open the case to read the MCU marking and look for SWD pads.
4. If the chip is an SN32F290 with exposed SWD pads, plan an SWD dump after checking its lock state. This is the unbrick backup that Path B needs.
5. Do not run SonixFlasherC or the F108 flasher on this board.

## Decisions for Jon

1. Is wired-only acceptable, or must wireless and the LCD stay? This decides B vs C.
2. Will you open the board and solder to the SWD pads?
3. Will you accept a possible brick with no tested recovery? If not, stay on A.

The full findings, including adversarial verification, are in workflow run `wf_79013e82-fad` (journal in the session transcript directory).
