#include <cstddef>
#include <iostream>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include "expression.h"
#include "logger.h"
#include "transport.h"
#include "handlers.h"

using json = nlohmann::json;


namespace {

template <typename T>
struct FormatName {
  const char* name;
  T value;
};

constexpr FormatName<QueryFormat> queryFormats[] = {
    {"arrayjson", QueryFormat::ArrayJson},
    {"objectjson", QueryFormat::ObjectJson},
};

constexpr FormatName<ResultFormat> resultFormats[] = {
    {"columnarjson", ResultFormat::ColumnarJson},
    {"typedcolumnarjson", ResultFormat::TypedColumnarJson},
    {"indexedcolumnarjson", ResultFormat::IndexedColumnarJson},
    {"positionalrowsjson", ResultFormat::PositionalRowsJson},
    {"arrayofobjectsjson", ResultFormat::ArrayOfObjectsJson},
    {"auto", ResultFormat::Auto},
};

// "on"/"off", not "true"/"false" -- matches the vocabulary eval/'s own
// --thinking on|off flags already use for this exact axis (theta).
constexpr FormatName<bool> thinkingValues[] = {
    {"off", false},
    {"on", true},
};

// `const FormatName<T> (&table)[N]` ~ "reference to an array of N"
// A plain `const FormatName<T>*` parameter would decay and lose the length
template <typename T, size_t N>
bool parseFormat(const std::string& v, const FormatName<T> (&table)[N], T& out) {
  for(const FormatName<T>& entry : table) {
    if(v == entry.name) {
      out = entry.value;
      return true;
    }
  }
  return false;
}

// "a or b" / "a, b, or c" — for the error message, generated from the table.
template <typename T, size_t N>
std::string acceptedValues(const FormatName<T> (&table)[N]) {
  std::string out;
  for(size_t i = 0; i < N; ++i) {
    if(i > 0) out += (N > 2) ? ", " : " ";
    if(i > 0 && i + 1 == N) out += "or ";
    out += table[i].name;
  }
  return out;
}

// Enforced ceiling on _meta["anthropic/maxResultSizeChars"]
// according to Anthropic's documentation
constexpr size_t maxAdvertisableResultChars = 500000;

// Parses --max-result-size-chars=N. Rejects anything non-numeric or negative
bool parseResultSizeChars(const std::string& v, size_t& out) {
  if(v.empty() || v.find_first_not_of("0123456789") != std::string::npos) return false;

  // Past the digit check, stoull can only fail if the value is too long for 
  // unsigned long long
  bool tooLong = false;
  try {
    out = static_cast<size_t>(std::stoull(v));
  } catch(const std::out_of_range&) {
    tooLong = true;
  }

  if(tooLong || out > maxAdvertisableResultChars) {
    std::cerr << "Note: --max-result-size-chars=" << v << " exceeds the host ceiling of "
              << maxAdvertisableResultChars << "; advertising " << maxAdvertisableResultChars
              << " instead." << std::endl;
    out = maxAdvertisableResultChars;
  }
  return true;
}

// The text after `flag` if `arg` starts with it, else nullopt
std::optional<std::string> flagValue(const std::string& arg, std::string_view flag) {
  if(arg.rfind(flag, 0) != 0) return std::nullopt;
  return arg.substr(flag.size());
}

// Reports a startup failure. Returns 1 so callers can `return fail(...)`,
// keeping each branch's failure path to a single line.
int fail(const std::string& message) {
  std::cerr << message << std::endl;
  return 1;
}

// One row per accepted flag — the same "single source of truth" shape as the
// format tables above, so the argument loop below has no per-flag branches to
// keep in sync. Adding a flag is one row.
//
// `parse` and `expected` are captureless lambdas, which convert to plain
// function pointers, so this table stays constexpr: no std::function, no type
// erasure, no allocation. Each `parse` adapts one underlying parser to the
// common (value, config) shape; each `expected` supplies the tail of the error
// message, generated from the format tables where there is one.
struct FlagSpec {
  const char* flag;
  const char* errorLabel;
  bool (*parse)(const std::string& value, ServerConfig& config);
  std::string (*expected)();
};

constexpr FlagSpec flagSpecs[] = {
    {"--query-format=", "Unknown query format",
     [](const std::string& v, ServerConfig& c) { return parseFormat(v, queryFormats, c.queryFormat); },
     [] { return acceptedValues(queryFormats); }},
    {"--result-format=", "Unknown result format",
     [](const std::string& v, ServerConfig& c) { return parseFormat(v, resultFormats, c.resultFormat); },
     [] { return acceptedValues(resultFormats); }},
    {"--max-result-size-chars=", "Invalid max result size",
     [](const std::string& v, ServerConfig& c) { return parseResultSizeChars(v, c.maxResultSizeChars); },
     [] { return std::string("a non-negative integer number of characters"); }},
    {"--default-thinking=", "Unknown thinking setting",
     [](const std::string& v, ServerConfig& c) { return parseFormat(v, thinkingValues, c.defaultThinking); },
     [] { return acceptedValues(thinkingValues); }},
};

}  // namespace


int main(int argc, char* argv[]) {
  // Defaults: array query in, plain columnar out,
  // host-default result-size limit; thinking mode disabled.
  // See ServerConfig in handlers.h.
  ServerConfig config;

  for(int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    bool matched = false;
    for(const FlagSpec& spec : flagSpecs) {
      std::optional<std::string> value = flagValue(arg, spec.flag);
      if(!value) continue;
      matched = true;
      if(!spec.parse(*value, config)) {
        return fail(std::string(spec.errorLabel) + ": " + *value +
                    " (expected " + spec.expected() + ")");
      }
      break;
    }
    if(!matched) return fail("Unknown argument: " + arg);
  }

  LogLevel logLevel = LogLevel::Info;

  while(true) {
    auto rawMessage = readMessage(std::cin);
    if(!rawMessage.has_value()) break;

    json request;
    try {
      request = json::parse(*rawMessage);
    } catch(const std::exception& e) {
      sendResponse(makeError(-32700, std::string("Parse error: ") + e.what(), nullptr));
      continue;
    }

    if(handleRequest(request, logLevel, config)) break;
  }

  return 0;
}
