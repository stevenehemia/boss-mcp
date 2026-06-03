#include <iostream>
#include "expression.h"
#include "logger.h"
#include "transport.h"
#include "handlers.h"

using json = nlohmann::json;

int main(int argc, char* argv[]) {
  Format format = Format::ExpressionJSON;
  for(int i = 1; i < argc; ++i) {
    const std::string arg = argv[i];
    if(arg == "--format=regular") { format = Format::Regular; }
    else if(arg == "--format=expressionjson") { format = Format::ExpressionJSON; }
    else {
      std::cerr << "Unknown argument: " << arg << std::endl;
      return 1;
    }
  }

  LoggerState logger;
  bool shuttingDown = false;

  while(!shuttingDown) {
    auto rawMessage = readMessage(std::cin);
    if(!rawMessage.has_value()) { break; }

    json request;
    try {
      request = json::parse(*rawMessage);
    } catch(const std::exception& e) {
      sendResponse(makeError(-32700, std::string("Parse error: ") + e.what(), nullptr));
      continue;
    }

    HandlerResult result = handleRequest(request, logger, format);
    if(result.shouldExit) { break; }
    if(result.shouldShutDown) { shuttingDown = true; }
  }

  return 0;
}
