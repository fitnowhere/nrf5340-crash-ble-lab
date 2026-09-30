/* Source for fixture.elf. Rebuild instructions are in service/tests/README.md. */
__attribute__((used, section(".diag_build_id")))
const char diagnostic_fingerprint[] = "0123456789abcdefabcd";

__attribute__((noinline)) void fixture_fault(void)
{
	__asm__ volatile("nop");
}

void _start(void)
{
	fixture_fault();
}
