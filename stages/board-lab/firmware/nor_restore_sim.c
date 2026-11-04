/* DLL facade so the host simulator can drive the REAL restore state machine
 * (nor_restore.c) instead of a Python rewrite of it. Built only for the host
 * test/simulation; never linked into the firmware image. */
#include "nor_restore.h"
#include <stdlib.h>

#if defined(_WIN32)
#define NR_API __declspec(dllexport)
#else
#define NR_API
#endif

typedef int (*sim_erase_fn)(uint32_t addr, void *user);
typedef int (*sim_program_fn)(uint32_t addr, const uint8_t *src, uint32_t n, void *user);
typedef int (*sim_read_fn)(uint32_t addr, uint8_t *out, uint32_t n, void *user);

typedef struct {
    nr_ctx_t ctx;
    nr_mailbox_t *mb;
    sim_erase_fn erase;
    sim_program_fn program;
    sim_read_fn read;
    void *user;
} sim_t;

static int a_erase(void *p, uint32_t addr)
{
    sim_t *s = (sim_t *)p;
    return s->erase(addr, s->user);
}

static int a_program(void *p, uint32_t addr, const uint8_t *src, uint32_t n)
{
    sim_t *s = (sim_t *)p;
    return s->program(addr, src, n, s->user);
}

static int a_read(void *p, uint32_t addr, uint8_t *out, uint32_t n)
{
    sim_t *s = (sim_t *)p;
    return s->read(addr, out, n, s->user);
}

NR_API void *nr_sim_create(uint32_t total, sim_erase_fn erase, sim_program_fn program,
                           sim_read_fn read, void *user)
{
    sim_t *s;
    nr_io_t io;

    if (!erase || !program || !read) return NULL;
    if (total == 0u || total > NR_TOTAL_SIZE || (total % NR_SECTOR_SIZE) != 0u) return NULL;
    s = (sim_t *)calloc(1, sizeof *s);
    if (!s) return NULL;
    s->mb = (nr_mailbox_t *)calloc(1, sizeof *s->mb);
    if (!s->mb) { free(s); return NULL; }
    s->erase = erase;
    s->program = program;
    s->read = read;
    s->user = user;
    io.erase = a_erase;
    io.program = a_program;
    io.read = a_read;
    io.ctx = s;
    nr_init(&s->ctx, s->mb, &io, total);
    return s;
}

NR_API int nr_sim_service(void *h)
{
    sim_t *s = (sim_t *)h;
    return s ? nr_service(&s->ctx) : -1;
}

NR_API void nr_sim_destroy(void *h)
{
    sim_t *s = (sim_t *)h;
    if (!s) return;
    free(s->mb);
    free(s);
}

NR_API uint8_t *nr_sim_mailbox(void *h)
{
    sim_t *s = (sim_t *)h;
    return s ? (uint8_t *)s->mb : NULL;
}

NR_API uint32_t nr_sim_total(void *h) { sim_t *s = (sim_t *)h; return s ? s->ctx.total : 0u; }
NR_API uint32_t nr_sim_blocks_done(void *h) { sim_t *s = (sim_t *)h; return s ? s->ctx.blocks_done : 0u; }
NR_API uint32_t nr_sim_bytes_done(void *h) { sim_t *s = (sim_t *)h; return s ? s->ctx.bytes_done : 0u; }
NR_API uint32_t nr_sim_state(void *h) { sim_t *s = (sim_t *)h; return s ? s->ctx.state : 0xFFu; }
NR_API uint32_t nr_sim_page_len(void *h) { sim_t *s = (sim_t *)h; return s ? s->ctx.page_len : 0u; }

/* Compile-time geometry, so the host never hard-codes a second copy. */
NR_API uint32_t nr_sim_magic(void) { return NR_MAGIC; }
NR_API uint32_t nr_sim_version(void) { return NR_VERSION; }
NR_API uint32_t nr_sim_header_bytes(void) { return NR_HEADER_BYTES; }
NR_API uint32_t nr_sim_block_size(void) { return NR_BLOCK_SIZE; }
NR_API uint32_t nr_sim_total_max(void) { return NR_TOTAL_SIZE; }
NR_API uint32_t nr_sim_sector_size(void) { return NR_SECTOR_SIZE; }
NR_API uint32_t nr_sim_page_size(void) { return NR_PAGE_SIZE; }
NR_API uint32_t nr_sim_cmd_restore(void) { return NR_CMD_RESTORE; }
NR_API uint32_t nr_sim_cmd_verify(void) { return NR_CMD_VERIFY; }
NR_API uint32_t nr_sim_st_idle(void) { return NR_ST_IDLE; }
NR_API uint32_t nr_sim_st_busy(void) { return NR_ST_BUSY; }
NR_API uint32_t nr_sim_st_ready(void) { return NR_ST_READY; }
NR_API uint32_t nr_sim_st_block_ok(void) { return NR_ST_BLOCK_OK; }
NR_API uint32_t nr_sim_st_done(void) { return NR_ST_DONE; }
NR_API uint32_t nr_sim_st_error(void) { return NR_ST_ERROR; }
