#include "handlers.h"
#include "logger.h"
#include "expression.h"
#include "transport.h"

using json = nlohmann::json;

constexpr const char* defaultProtocolVersion = "2025-11-25";
constexpr const char* serverName = "boss-mcp";
constexpr const char* serverVersion = "0.1.0";

namespace {

json toolResponse(const std::string& text, bool isError) {
  return {{"content", json::array({{{"type", "text"}, {"text", text}}})}, {"isError", isError}};
}

json buildToolsList(Format format) {
  constexpr const char* evaluateDescription =
    "Evaluate a BOSS expression. Call boss_describe first to get the complete operator reference. "
    "Load CSV: [\"Load\", [\"String\", \"/absolute/path/to/file.csv\"]] — always use absolute paths. "
    "In-memory table: [\"Table\", [\"ColName\", val1, val2, ...]]. "
    "Column reference: [\"Symbol\", \"colname\"]. "
    "Type cast: [\"Int\", [\"Symbol\", \"col\"]]. "
    "Multi-step queries: Name(table, [\"Symbol\", \"label\"]) stores a result; ByName([\"Symbol\", \"label\"]) retrieves it.";

  const std::string expressionDescription = format == Format::ExpressionJSON
    ? R"(ExpressionJSON array format. )"
      R"(Example (load CSV and filter rows where new_cases_per_million > 1000): )"
      R"(["Filter", ["Load", ["String", "/absolute/path/to/data.csv"]], ["Greater", ["Symbol", "new_cases_per_million"], ["Integer", 1000]]])"
    : R"(BOSS expression as a nested JSON object with "type", "head", and "args" fields. )"
      R"(Column references use {"type":"symbol","value":"colname"}. )"
      R"(Example (load CSV and filter rows where new_cases_per_million > 1000): )"
      R"({"type":"call","head":"Filter","args":[{"type":"call","head":"Load","args":[{"type":"string","value":"/absolute/path/to/data.csv"}]},{"type":"call","head":"Greater","args":[{"type":"symbol","value":"new_cases_per_million"},{"type":"long","value":1000}]}]})";

  const char* expressionType = format == Format::ExpressionJSON ? "array" : "object";

  json evaluateTool;
  evaluateTool["name"] = "boss_evaluate";
  evaluateTool["description"] = evaluateDescription;
  evaluateTool["inputSchema"] = {
      {"type", "object"},
      {"properties", {{"expression", {{"type", expressionType}, {"description", expressionDescription}}}}},
      {"required", json::array({"expression"})}};

  json describeTool;
  describeTool["name"] = "boss_describe";
  describeTool["description"] =
    "Returns the complete BOSS operator reference directly from the engine. "
    "Call this before boss_evaluate to discover all available operations and their exact syntax.";
  describeTool["inputSchema"] = {{"type", "object"}, {"properties", json::object()}};

  return json::array({evaluateTool, describeTool});
}

json handleDescribeCall(const LogLevel& logLevel) {
  SymbolPtr sym(symbolNameToNewBOSSSymbol("GetEngineDescription"));
  ExprPtr expr(newComplexBOSSExpression(sym.get(), 0, nullptr));

  std::string description;
  try {
    // BOSSEvaluate consumes its input expression.
    ExprPtr result(BOSSEvaluate(expr.release()));
    description = toString(getNewStringValueFromBOSSExpression(result.get()));
  } catch(...) {
    return toolResponse("GetEngineDescription failed", true);
  }

  if(description.empty()) {
    return toolResponse("engine description unavailable", true);
  }

  logMessage(logLevel, LogLevel::kDebug, "boss_describe called");
  return toolResponse(description, false);
}

json handleToolsCall(const json& params, const LogLevel& logLevel, Format format) {
  if(!params.contains("name") || !params["name"].is_string()) {
    return toolResponse("missing tool name", true);
  }

  const std::string name = params["name"].get<std::string>();
  if(name == "boss_describe") {
    return handleDescribeCall(logLevel);
  }
  if(name != "boss_evaluate") {
    return toolResponse("unknown tool", true);
  }

  if(!params.contains("arguments") || !params["arguments"].is_object()) {
    return toolResponse("missing arguments", true);
  }

  const json& arguments = params["arguments"];
  if(!arguments.contains("expression")) {
    return toolResponse("missing expression", true);
  }

  std::string error;
  ExprPtr expression = parseExpression(arguments["expression"], format, error);
  if(!expression) {
    return toolResponse(error, true);
  }

  json resultJson;
  try {
    // BOSSEvaluate consumes its input expression.
    ExprPtr result(BOSSEvaluate(expression.release()));
    resultJson = expressionToJson(result.get(), format);
  } catch(const std::exception& e) {
    return toolResponse(std::string("BOSS evaluation error: ") + e.what(), true);
  } catch(...) {
    return toolResponse("BOSS evaluation error: unknown exception", true);
  }

  logMessage(logLevel, LogLevel::kDebug, "Evaluated expression using boss_evaluate");
  return toolResponse(resultJson.dump(), false);
}

} // namespace

bool handleRequest(const json& request, LogLevel& logLevel, Format format) {
  const std::string method = request.value("method", "");

  if(method == "initialized" || method == "notifications/initialized") { return false; }
  if(method == "exit") { return true; }

  if(!request.contains("id")) { return false; }
  const json id = request["id"];

  if(method == "shutdown") {
    sendResponse(makeResult(id, nullptr));
    return true;
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
    sendResponse(makeResult(id, result));
    return false;
  }

  if(method == "logging/setLevel") {
    if(request.contains("params") && request["params"].is_object()) {
      logLevel = parseLogLevel(request["params"].value("level", "info"));
    }
    sendResponse(makeResult(id, json::object()));
    return false;
  }

  if(method == "tools/list") {
    sendResponse(makeResult(id, {{"tools", buildToolsList(format)}}));
    return false;
  }

  if(method == "tools/call") {
    if(!request.contains("params") || !request["params"].is_object()) {
      sendResponse(makeError(-32602, "Missing params", id));
      return false;
    }
    sendResponse(makeResult(id, handleToolsCall(request["params"], logLevel, format)));
    return false;
  }

  sendResponse(makeError(-32601, "Method not found", id));
  return false;
}
