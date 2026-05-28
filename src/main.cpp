#include <fstream>
#include <iostream>
#include <sstream>
#include "logger.h"
#include "transport.h"
#include "handlers.h"

int main() {
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

    HandlerResult result = handleRequest(request, logger);
    if(result.shouldExit) {
      break;
    }
    if(result.shouldShutDown) {
      shuttingDown = true;
    }
  }

  return 0;
}