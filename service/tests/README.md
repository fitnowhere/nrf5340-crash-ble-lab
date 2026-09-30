# Tracked service fixture

`fixtures/fixture.elf` is a 32-bit little-endian ARM ELF with DWARF and the
test fingerprint `0123456789abcdefabcd`. It keeps successful ingestion and
symbolication covered in a clean clone; ignored DK archives only add optional
hardware-regression coverage.

Rebuild with an ARM GNU toolchain:

```sh
arm-none-eabi-gcc -mcpu=cortex-m33 -mthumb -g -Og -ffreestanding \
  -c service/tests/fixtures/fixture.c -o /tmp/fixture.o
arm-none-eabi-ld -Ttext=0x1000 -e _start /tmp/fixture.o \
  -o service/tests/fixtures/fixture.elf
```
