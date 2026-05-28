#include <fstream>
#include "handlers.h"
#include "logger.h"
#include "expression.h"
#include "transport.h"

constexpr const char* defaultProtocolVersion = "2025-11-25";
constexpr const char* serverName = "boss-mcp";
constexpr const char* serverVersion = "0.1.0";

json buildToolsList() {
  json tool;
  tool["name"] = "boss_evaluate";
  tool["description"] = "Evaluate a BOSS expression using the in-process engine.";
  tool["inputSchema"] = {
      {"type", "object"},
      {"properties",
       {{"expression",
         {{"type", "object"},
          {"description",
           "BOSS expression JSON. Example: {\"type\":\"call\",\"head\":\"Plus\",\"args\":[{\"type\":\"long\",\"value\":1},{\"type\":\"long\",\"value\":2}]}"}}}}},
      {"required", json::array({"expression"})}};

  return json::array({tool});
}

json handleToolsCall(const json& params, LoggerState& logger) {
  if(!params.contains("name") || !params["name"].is_string()) {
    return {{"content", json::array({{{"type", "text"}, {"text", "missing tool name"}}})},
            {"isError", true}};
  }

  const std::string name = params["name"].get<std::string>();
  if(name != "boss_evaluate") {
    return {{"content", json::array({{{"type", "text"}, {"text", "unknown tool"}}})},
            {"isError", true}};
  }

  if(!params.contains("arguments") || !params["arguments"].is_object()) {
    return {{"content", json::array({{{"type", "text"}, {"text", "missing arguments"}}})},
            {"isError", true}};
  }

  const json& arguments = params["arguments"];
  if(!arguments.contains("expression")) {
    return {{"content", json::array({{{"type", "text"}, {"text", "missing expression"}}})},
            {"isError", true}};
  }

  std::string error;
  BOSSExpression* expression = parseExpression(arguments["expression"], error);
  if(expression == nullptr) {
    return {{"content", json::array({{{"type", "text"}, {"text", error}}})},
            {"isError", true}};
  }

  BOSSExpression* result = BOSSEvaluate(expression);
  json resultJson = expressionToJson(result);
  freeBOSSExpression(result);

  logMessage(logger, LogLevel::kDebug, "Evaluated expression using boss_evaluate");

  json payload;
  payload["content"] = json::array({{{"type", "text"}, {"text", resultJson.dump()}}});
  payload["isError"] = false;
  return payload;
}

HandlerResult handleRequest(const json& request, LoggerState& logger) {
  const bool hasId  = request.contains("id");
  if(!hasId) { return {}; }

  const json id = hasId ? request["id"] : json(nullptr);
  const std::string method = request.value("method", "");

  if(method == "initialized" || method == "notifications/initialized") {
    logger.clientInitialized = true;
    return {};
  }

  if(method == "exit") { return {.shouldExit = true}; }

  if(method == "shutdown") {
    sendResponse(makeResult(id, nullptr));
    return {.shouldShutDown = true};
  }

  if(method == "initialize") {
    std::string protocolVersion = defaultProtocolVersion;
    if(request.contains("params") && request["params"].is_object()) {
      const json& params = request["params"];
      if(params.contains("protocolVersion") && params["protocolVersion"].is_string()) {
        protocolVersion = params["protocolVersion"].get<std::string>();
      }
    }
    json result;
    result["protocolVersion"] = protocolVersion;
    result["serverInfo"] = {{"name", serverName}, {"version", serverVersion}};
    result["capabilities"] = {
      {"tools",   {{"listChanged", false}}},
      {"logging", json::object()}
    };
    logMessage(logger, LogLevel::kInfo,
               std::string("Initialize response: ") + makeResult(id, result).dump());
    {
      std::ofstream initLog("/tmp/boss-mcp-init.log", std::ios::app);
      if(initLog) {
        initLog << makeResult(id, result).dump() << '\n';
      }
    }
    sendResponse(makeResult(id, result));
    return {};
  }

  if(method == "logging/setLevel") {
    if(request.contains("params") && request["params"].is_object()) {
      logger.level = parseLogLevel(request["params"].value("level", "info"));
    }
    sendResponse(makeResult(id, json::object()));
    return {};
  }

  if(method == "tools/list") {
    sendResponse(makeResult(id, {{"tools", buildToolsList()}}));
    return {};
  }

  if(method == "tools/call") {
    if(!request.contains("params") || !request["params"].is_object()) {
      sendResponse(makeError(-32602, "Missing params", id));
      return {};
    }
    sendResponse(makeResult(id, handleToolsCall(request["params"], logger)));
    return {};
  }

  sendResponse(makeError(-32601, "Method not found", id));
  
  return {};
}
