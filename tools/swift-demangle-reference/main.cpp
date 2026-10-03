// A line-per-name front end mirroring tools/swift-demangle/swift-demangle.cpp for one
// name (not its stdin substring-filter mode); see README.md.
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

  // The tool's other five option assignments are hidden test knobs whose command-line
  // defaults equal the struct's own, so they are omitted.
  swift::Demangle::DemangleOptions options;
  if (simplified)
    options = swift::Demangle::DemangleOptions::SimplifiedUIDemangleOptions();
  else
    options.SynthesizeSugarOnTypes = true;

  swift::Demangle::Context context;
  std::string line;
  while (std::getline(std::cin, line)) {
    // As swift-demangle does: strip a Mach-O symbol's extra underscore, and on failure
    // print the *stripped* form back, not the original.
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
