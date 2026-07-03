#include <iostream>
#include "expression.h"
#include "logger.h"
#include "transport.h"
#include "handlers.h"

using json = nlohmann::json;


static bool parseQueryFormat(const std::string& v, QueryFormat& out) {
  if(v == "arrayjson") {
    out = QueryFormat::ArrayJson;
    return true;
  }
  if(v == "objectjson") {
    out = QueryFormat::ObjectJson;
    return true;
  }
  return false;
}


static bool parseResultFormat(const std::string& v, ResultFormat& out) {
  if(v == "columnarjson") {
    out = ResultFormat::ColumnarJson;
    return true;
  }
  if(v == "rowrepjson") {
    out = ResultFormat::RowRepJson;
    return true;
  }
  return false;
}


int main(int argc, char* argv[]) {
  // Default: BOSS-native on both sides (array query in, columnar out)
  QueryFormat queryFormat = QueryFormat::ArrayJson;
  ResultFormat resultFormat = ResultFormat::ColumnarJson;

  const std::string Q = "--query-format=", R = "--result-format=";

  for(int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if(arg.rfind(Q, 0) == 0) {
      if(!parseQueryFormat(arg.substr(Q.size()), queryFormat)) {
        std::cerr << "Unknown query format: " << arg.substr(Q.size())
                  << " (expected arrayjson or objectjson)" << std::endl;
        return 1;
      }
    } else if(arg.rfind(R, 0) == 0) {
      if(!parseResultFormat(arg.substr(R.size()), resultFormat)) {
        std::cerr << "Unknown result format: " << arg.substr(R.size())
                  << " (expected columnarjson or rowrepjson)" << std::endl;
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
