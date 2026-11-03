/* ST's GCC startup calls __libc_init_array. Newlib invokes these platform
 * hooks; C static constructors still run through the standard array walker.
 * No semihosting, UART retarget or heap service is added here. */
void _init(void) {}
void _fini(void) {}
