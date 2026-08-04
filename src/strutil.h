#pragma once
#include <cctype>
#include <string>

// Small string helpers shared across modules. Header-only: these are used by
// transport, logger, and expression, and none of them should have to depend on
// another module's header just to lowercase a string.

// ASCII-lowercases a copy. Takes by value so callers can move into it.
inline std::string toLower(std::string value) {
  for(char& c : value) {
    c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  }
  return value;
}
