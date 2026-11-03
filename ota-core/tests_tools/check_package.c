/* Interoperability check: C core validates a package produced by Python. */
#include "ota/image.h"
#include <stdio.h>
#include <string.h>
static unsigned char bytes[4096];
static uint32_t loaded;
static int read_bytes(void *u,uint32_t off,uint8_t *out,uint32_t n)
{
    (void)u;
    if(off>loaded || n>loaded-off) return -1;
    memcpy(out,bytes+off,n); return 0;
}
int main(int argc,char **argv)
{
    FILE *fp;
    flash_port_t flash;
    uint32_t version,size;
    if(argc!=2 || !(fp=fopen(argv[1],"rb"))) return 2;
    loaded=(uint32_t)fread(bytes,1,sizeof bytes,fp); fclose(fp);
    memset(&flash,0,sizeof flash); flash.read=read_bytes;
    if(image_validate(flash,DOWNLOAD_OFFSET,&version,&size)) {
        puts("C validator rejected package"); return 1;
    }
    printf("C validator accepted Python IMG2: version=%u payload=%u\n",
           (unsigned)version,(unsigned)size); return 0;
}
