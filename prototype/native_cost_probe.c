/* Diagnostic-only function costs. Compile this object without instrumentation.
 * Single-threaded bounded workload; nested selected functions are not double
 * counted in exclusive time. Clock/probe overhead remains in measured costs.
 */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <time.h>
#include <dlfcn.h>

typedef struct { void *fn; uint64_t calls, total, own; } Entry;
typedef struct { Entry *entry; uint64_t start, children; } Frame;
static Entry entries[16384];
static Frame frames[4096];
static unsigned depth;
static uint64_t imbalance;
static uint64_t now(void){
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (uint64_t)ts.tv_sec*1000000000 + ts.tv_nsec;
}
void __cyg_profile_func_enter(void *fn, void *caller){
  unsigned i=((uintptr_t)fn >> 2)&16383;
  (void)caller;
  while(entries[i].fn && entries[i].fn!=fn) i=(i+1)&16383;
  entries[i].fn=fn; entries[i].calls++;
  if(depth>=4096){ fputs("probe depth exceeded\n",stderr); abort(); }
  frames[depth++]=(Frame){ &entries[i], now(), 0 };
}
void __cyg_profile_func_exit(void *fn, void *caller){
  uint64_t end=now(), elapsed;
  Frame frame;
  (void)caller;
  if(!depth){ imbalance++; return; }
  frame=frames[--depth];
  if(frame.entry->fn!=fn){ imbalance++; return; }
  elapsed=end-frame.start;
  frame.entry->total+=elapsed;
  frame.entry->own+=elapsed-frame.children;
  if(depth) frames[depth-1].children+=elapsed;
}
__attribute__((destructor)) static void report(void){
  const char *path=getenv("REVERSEDB_PROFILE");
  FILE *out;
  unsigned i;
  Dl_info info;
  if(!path) return;
  out=fopen(path,"w"); if(!out) return;
  dladdr((void*)&__cyg_profile_func_enter,&info);
  fprintf(out,"base %p depth %u imbalance %llu\n",info.dli_fbase,depth,(unsigned long long)imbalance);
  for(i=0;i<16384;i++) if(entries[i].fn){
    Entry *e=&entries[i];
    fprintf(out,"%p %llu %llu %llu\n",e->fn,(unsigned long long)e->calls,
            (unsigned long long)e->total,(unsigned long long)e->own);
  }
  fclose(out);
}
