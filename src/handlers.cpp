#include "handlers.h"
#include "logger.h"
#include "expression.h"
#include "transport.h"

using json = nlohmann::json;

constexpr const char* defaultProtocolVersion = "2025-11-25";
constexpr const char* serverName = "boss-mcp";
constexpr const char* serverVersion = "0.1.0";

namespace {

json buildToolsList(Format format) {
  constexpr const char* toolDescription =
    "Evaluate a BOSS expression. BOSS is a relational query engine for tabular data. "

    "DATA SOURCES: "
    "Load a CSV file with [\"Load\", [\"String\", \"/absolute/path/to/file.csv\"]] — always use absolute paths. "
    "Build an in-memory table with [\"Table\", [\"ColName\", val1, val2, ...]]. "

    "CORE OPERATIONS: "
    "Filter(table, predicate) — row filtering. "
    "Project(table, col1, col2, ...) — column selection; wrap a column in a type cast e.g. [\"Int\", [\"Symbol\", \"col\"]] to convert its type. "
    "OrderBy(table, [\"keys\", col1, col2]) — sort rows. "
    "Materialize(expr) — force evaluation of a lazy expression. "

    "AGGREGATION: "
    "GroupBy(table, aggregate, [\"keys\", groupCol]) — group and aggregate; e.g. [\"max\", [\"Symbol\", \"col\"]]. "
    "Cumulate(table, aggregate) — running/cumulative aggregation over a column. "
    "Pairwise(table, [\"Symbol\", \"outputCol\"], aggregate, [\"Integer\", windowSize]) — rolling window aggregation. "

    "JOINS: "
    "Join(left, right, [\"keys\", joinCol]) — joins on a shared key column; appends _l/_r suffixes to disambiguate columns. "

    "NAMED RESULTS (multi-step queries): "
    "Name(expr, [\"Symbol\", \"label\"]) — assigns a name to a result so it can be referenced later. "
    "ByName([\"Symbol\", \"label\"]) — retrieves a previously named result. "

    "PREDICATES (use inside Filter): Greater, Less, Equal, And. "

    "COLUMN REFERENCES: [\"Symbol\", \"colname\"]. "

    "NOT SUPPORTED (returns expression unevaluated): Plus, Times, Map/Lambda.";

  const std::string expressionDescription = format == Format::ExpressionJSON
    ? R"(ExpressionJSON array format. )"
      R"(Example (load CSV and filter rows where new_cases_per_million > 1000): )"
      R"(["Filter", ["Load", ["String", "/absolute/path/to/data.csv"]], ["Greater", ["Symbol", "new_cases_per_million"], ["Integer", 1000]]])"
    : R"(BOSS expression as a nested JSON object with "type", "head", and "args" fields. )"
      R"(Column references use {"type":"symbol","value":"colname"}. )"
      R"(Example (load CSV and filter rows where new_cases_per_million > 1000): )"
      R"({"type":"call","head":"Filter","args":[{"type":"call","head":"Load","args":[{"type":"string","value":"/absolute/path/to/data.csv"}]},{"type":"call","head":"Greater","args":[{"type":"symbol","value":"new_cases_per_million"},{"type":"long","value":1000}]}]})";

  const char* expressionType = format == Format::ExpressionJSON ? "array" : "object";

  json tool;
  tool["name"] = "boss_evaluate";
  tool["description"] = toolDescription;
  tool["inputSchema"] = {
      {"type", "object"},
      {"properties", {{"expression", {{"type", expressionType}, {"description", expressionDescription}}}}},
      {"required", json::array({"expression"})}};

  return json::array({tool});
}

json handleToolsCall(const json& params, LoggerState& logger, Format format) {
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
  BOSSExpression* expression = parseExpression(arguments["expression"], format, error);
  if(expression == nullptr) {
    return {{"content", json::array({{{"type", "text"}, {"text", error}}})},
            {"isError", true}};
  }

  BOSSExpression* result = BOSSEvaluate(expression);
  json resultJson = expressionToJson(result, format);
  freeBOSSExpression(result);

  logMessage(logger, LogLevel::kDebug, "Evaluated expression using boss_evaluate");

  return {{"content", json::array({{{"type", "text"}, {"text", resultJson.dump()}}})},
          {"isError", false}};
}

} // namespace

HandlerResult handleRequest(const json& request, LoggerState& logger, Format format) {
  const std::string method = request.value("method", "");

  if(method == "initialized" || method == "notifications/initialized") {
    logger.clientInitialized = true;
    return {};
  }

  if(method == "exit") { return {.shouldExit = true}; }

  if(!request.contains("id")) { return {}; }
  const json id = request["id"];

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
    sendResponse(makeResult(id, {{"tools", buildToolsList(format)}}));
    return {};
  }

  if(method == "tools/call") {
    if(!request.contains("params") || !request["params"].is_object()) {
      sendResponse(makeError(-32602, "Missing params", id));
      return {};
    }
    sendResponse(makeResult(id, handleToolsCall(request["params"], logger, format)));
    return {};
  }

  sendResponse(makeError(-32601, "Method not found", id));
  return {};
}
