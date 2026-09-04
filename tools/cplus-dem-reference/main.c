/* A line-per-name front end over libiberty's pre-Itanium demangler, so the differential
   tools can ask it the way they ask `llvm-cxxfilt` and `c++filt`: one name in, one
   spelling out, a name it cannot read handed back unchanged.

   What `c++filt --format=<style>` did to one name before binutils dropped the styles:
   `cplus_demangle_set_style` from the name given, then `cplus_demangle` under
   `DMGL_PARAMS | DMGL_ANSI` -- the defaults c++filt applies, and the settings
   tests/conformance/gnuv2-libiberty.txt records in its third column. `--no-params` is
   the fourth column, and `--no-ansi` and `--verbose` are the two flags c++filt exposed
   beside it.

       cplus-dem-reference [gnu|lucid|arm|hp|edg|auto] [--no-params] [--no-ansi] [--verbose] < names

   `gnu` is the default, as it is the default of the scheme this is the oracle for. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "demangle.h"

int main(int argc, char **argv) {
  int options = DMGL_PARAMS | DMGL_ANSI;
  enum demangling_styles style = gnu_demangling;
  for (int i = 1; i < argc; ++i) {
    if (!strcmp(argv[i], "--no-params")) {
      options &= ~DMGL_PARAMS;
    } else if (!strcmp(argv[i], "--no-ansi")) {
      options &= ~DMGL_ANSI;
    } else if (!strcmp(argv[i], "--verbose")) {
      options |= DMGL_VERBOSE;
    } else {
      style = cplus_demangle_name_to_style(argv[i]);
      if (style == unknown_demangling) {
        fprintf(stderr, "usage: cplus-dem-reference [gnu|lucid|arm|hp|edg|auto] [--no-params] [--no-ansi] [--verbose] < names\n");
        return 2;
      }
    }
  }
  cplus_demangle_set_style(style);

  char *line = NULL;
  size_t capacity = 0;
  ssize_t length;
  while ((length = getline(&line, &capacity, stdin)) > 0) {
    if (line[length - 1] == '\n') {
      line[length - 1] = '\0';
    }
    char *spelled = cplus_demangle(line, options);
    puts(spelled ? spelled : line);
    free(spelled);
  }
  free(line);
  return 0;
}
