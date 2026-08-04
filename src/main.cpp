#include <cstddef>
#include <iostream>
#include <string>
#include "expression.h"
#include "logger.h"
#include "transport.h"
#include "handlers.h"

using json = nlohmann::json;


namespace {

// The accepted --query-format=/--result-format= values. Adding a format is one
// row here; both the parser and the error message read from the same table, so
// the two can't drift apart.
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
};

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

}  // namespace


int main(int argc, char* argv[]) {
  // Default: BOSS-native array query in; plain columnar out. Pass
  // --result-format=typedcolumnarjson for the fully tagged ExpressionJSON form.
  QueryFormat queryFormat = QueryFormat::ArrayJson;
  ResultFormat resultFormat = ResultFormat::ColumnarJson;

  const std::string Q = "--query-format=", R = "--result-format=";

  for(int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if(arg.rfind(Q, 0) == 0) {
      if(!parseFormat(arg.substr(Q.size()), queryFormats, queryFormat)) {
        std::cerr << "Unknown query format: " << arg.substr(Q.size())
                  << " (expected " << acceptedValues(queryFormats) << ")" << std::endl;
        return 1;
      }
    } else if(arg.rfind(R, 0) == 0) {
      if(!parseFormat(arg.substr(R.size()), resultFormats, resultFormat)) {
        std::cerr << "Unknown result format: " << arg.substr(R.size())
                  << " (expected " << acceptedValues(resultFormats) << ")" << std::endl;
        return 1;
      }
    } else {
      std::cerr << "Unknown argument: " << arg << std::endl;
      return 1;
    }
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

    if(handleRequest(request, logLevel, queryFormat, resultFormat)) break;
  }

  return 0;
}
