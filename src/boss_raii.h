#pragma once
#include <memory>
#include <string>
#include "BOSS.h"

// RAII wrappers around the BOSS C API. Each owns a resource allocated by a
// new*/get*New* call and releases it via the matching free* in its destructor,
// so error and exception paths can no longer leak or double-free.

struct BOSSExpressionDeleter {
  void operator()(BOSSExpression* e) const { freeBOSSExpression(e); }
};
using ExprPtr = std::unique_ptr<BOSSExpression, BOSSExpressionDeleter>;

struct BOSSSymbolDeleter {
  void operator()(BOSSSymbol* s) const { freeBOSSSymbol(s); }
};
using SymbolPtr = std::unique_ptr<BOSSSymbol, BOSSSymbolDeleter>;

struct BOSSStringDeleter {
  void operator()(char* s) const { freeBOSSString(s); }
};
using StringPtr = std::unique_ptr<char, BOSSStringDeleter>;

// The arguments array is owned, but its elements are borrowed from the parent
// expression — freeBOSSArguments releases the array only.
struct BOSSArgumentsDeleter {
  void operator()(BOSSExpression** a) const { freeBOSSArguments(a); }
};
using ArgsPtr = std::unique_ptr<BOSSExpression*, BOSSArgumentsDeleter>;

// Copies a char* returned by the BOSS API into a std::string, taking ownership
// of the original (works for the const char* getters too — cast at the call).
inline std::string toString(char* raw) {
  StringPtr owned(raw);
  return owned ? owned.get() : "";
}
