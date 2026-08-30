// A line-per-name front end over Swift's own demangler, so the differential tools can
// ask it the way they ask `llvm-cxxfilt` and `c++filt`: one name in, one spelling out,
// a name it cannot read handed back unchanged.
//
// What `tools/swift-demangle/swift-demangle.cpp` does to one name, and nothing else:
// its options (`SynthesizeSugarOnTypes` on unless `--simplified`), its one-underscore
// strip for a name beginning `__`, `Context::demangleSymbolAsNode`, `nodeToString`,
// and its fallback of printing the name back when the spelling comes out empty.
//
// Deliberately not the tool's stdin mode. That scans each line for maybe-mangled
// *substrings* and substitutes them in place, which is a filter over text rather than a
// demangler over names; here one line is one name, which is how the corpora are scored.
#include "swift/Demangling/Demangle.h"

#include <iostream>
#include <string>

int main(int argc, char **argv) {
  bool simplified = false;
  for (int i = 1; i < argc; ++i) {
    std::string flag(argv[i]);
    if (flag == "--simplified") {
      simplified = true;
    } else {
      std::cerr << "usage: swift-demangle-reference [--simplified] < names\n";
      return 2;
    }
  }

  // The tool's own setup: sugar unless `-no-sugar`, then `-simplified` replacing the
  // whole set. The five options it assigns after that -- DisplayStdlibModule,
  // DisplayObjCModule, HidingCurrentModule, DisplayLocalNameContexts,
  // ShowClosureSignature -- are hidden test knobs whose command-line defaults are the
  // struct's own defaults, and SimplifiedUIDemangleOptions does not touch any of them,
  // so assigning them here would be a no-op either way.
  swift::Demangle::DemangleOptions options;
  if (simplified)
    options = swift::Demangle::DemangleOptions::SimplifiedUIDemangleOptions();
  else
    options.SynthesizeSugarOnTypes = true;

  swift::Demangle::Context context;
  std::string line;
  while (std::getline(std::cin, line)) {
    // swift-demangle strips one leading underscore so that a Mach-O symbol -- which
    // carries an extra one -- reads as the name the compiler mangled. When such a name
    // does not demangle the tool prints the *stripped* form back, not the original; only
    // its remangling modes put the underscore back. This does the same.
    llvm::StringRef name(line);
    if (line.compare(0, 2, "__") == 0)
      name = name.substr(1);
    swift::Demangle::NodePointer node = context.demangleSymbolAsNode(name);
    std::string spelled = swift::Demangle::nodeToString(node, options);
    std::cout << (spelled.empty() ? name.str() : spelled) << "\n";
    // Each line is its own name: no substitution state carries between them.
    context.clear();
  }
  return 0;
}
