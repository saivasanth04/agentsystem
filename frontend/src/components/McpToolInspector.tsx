import React, { useState, useEffect } from 'react';
import { 
  Server, 
  Wrench, 
  Play, 
  AlertTriangle, 
  ChevronRight, 
  RefreshCw,
  Sparkles
} from 'lucide-react';
import { orchestratorApi } from '../api/client';
import { McpServerInfo, McpTool } from '../types/orchestrator';

export const McpToolInspector: React.FC = () => {
  const [servers, setServers] = useState<McpServerInfo[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [selectedTool, setSelectedTool] = useState<{ server: string; tool: McpTool } | null>(null);
  const [toolArgs, setToolArgs] = useState<string>('{}');
  const [executing, setExecuting] = useState(false);
  const [execResult, setExecResult] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);

  const fetchServers = async () => {
    setIsLoading(true);
    setError(null);
    try {
      const data = await orchestratorApi.getMcpServers();
      setServers(data.servers || []);
    } catch (err: any) {
      setError(err.message || 'Failed to load MCP servers');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchServers();
  }, []);

  const handleSelectTool = (serverName: string, tool: McpTool) => {
    setSelectedTool({ server: serverName, tool });
    setExecResult(null);
    // Generate starter args from parameters if available
    try {
      const schema = tool.parameters || (tool as any).input_schema;
      if (schema && schema.properties) {
        const sample: Record<string, any> = {};
        for (const [key, prop] of Object.entries<any>(schema.properties)) {
          sample[key] = prop.default !== undefined ? prop.default : (prop.type === 'number' ? 0 : prop.type === 'boolean' ? false : '');
        }
        setToolArgs(JSON.stringify(sample, null, 2));
      } else {
        setToolArgs('{}');
      }
    } catch {
      setToolArgs('{}');
    }
  };

  const handleExecute = async () => {
    if (!selectedTool || executing) return;
    setExecuting(true);
    setExecResult(null);
    try {
      let parsedArgs = {};
      try {
        parsedArgs = JSON.parse(toolArgs);
      } catch (jsonErr: any) {
        setExecResult({ error: `Invalid JSON arguments: ${jsonErr.message}` });
        setExecuting(false);
        return;
      }

      const res = await orchestratorApi.callMcpTool(selectedTool.server, selectedTool.tool.name, parsedArgs);
      setExecResult(res);
    } catch (err: any) {
      setExecResult({
        error: err.message || 'Tool execution failed'
      });
    } finally {
      setExecuting(false);
    }
  };

  return (
    <div className="bg-slate-900 border border-slate-800 rounded-2xl p-5 shadow-xl space-y-5">
      <div className="flex items-center justify-between border-b border-slate-800 pb-4">
        <div className="flex items-center space-x-3">
          <div className="p-2.5 rounded-xl bg-cyan-500/10 text-cyan-400 border border-cyan-500/20">
            <Server className="w-5 h-5" />
          </div>
          <div>
            <h3 className="font-bold text-slate-100 text-base font-sans">Model Context Protocol (MCP) Tools</h3>
            <p className="text-xs text-slate-400 font-mono">Inspect registered MCP servers, capability schemas, and execute direct test calls</p>
          </div>
        </div>

        <button
          onClick={fetchServers}
          disabled={isLoading}
          className="flex items-center space-x-1.5 px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg text-xs font-medium border border-slate-700 transition-colors disabled:opacity-50 font-mono"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin text-cyan-400' : ''}`} />
          <span>Refresh</span>
        </button>
      </div>

      {error && (
        <div className="p-3 bg-rose-500/10 border border-rose-500/30 rounded-xl text-rose-300 text-xs flex items-center space-x-2 font-mono">
          <AlertTriangle className="w-4 h-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {isLoading ? (
        <div className="py-12 flex flex-col items-center justify-center space-y-3 text-slate-500 font-mono">
          <RefreshCw className="w-6 h-6 animate-spin text-cyan-500" />
          <span className="text-xs">Querying MCP protocol servers...</span>
        </div>
      ) : servers.length === 0 ? (
        <div className="py-10 text-center text-slate-500 text-xs space-y-2 font-mono">
          <Server className="w-8 h-8 mx-auto stroke-1 text-slate-600" />
          <p>No MCP servers registered or active in configuration.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-5">
          {/* Left Column: Server & Tools List */}
          <div className="lg:col-span-5 space-y-3 max-h-[520px] overflow-y-auto pr-1 font-mono">
            {servers.map(server => (
              <div key={server.name} className="border border-slate-800 rounded-xl overflow-hidden bg-slate-950/40">
                <div className="p-3 bg-slate-900/80 border-b border-slate-800/80 flex items-center justify-between text-xs">
                  <div className="flex items-center space-x-2 font-semibold text-slate-200">
                    <Server className="w-4 h-4 text-cyan-400" />
                    <span>{server.name}</span>
                    <span className="text-[10px] px-1.5 py-0.2 bg-slate-800 text-slate-400 rounded">
                      {server.tools.length} tools
                    </span>
                  </div>
                  <span className={`px-2 py-0.5 rounded text-[10px] font-medium ${
                    server.status === 'CONNECTED' 
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' 
                      : 'bg-slate-800 text-slate-400'
                  }`}>
                    {server.status}
                  </span>
                </div>

                <div className="p-1.5 space-y-1">
                  {server.tools.length === 0 ? (
                    <div className="p-3 text-center text-slate-500 text-[11px]">No tools declared</div>
                  ) : (
                    server.tools.map(tool => {
                      const isSelected = selectedTool?.server === server.name && selectedTool?.tool.name === tool.name;
                      return (
                        <button
                          key={tool.name}
                          onClick={() => handleSelectTool(server.name, tool)}
                          className={`w-full text-left p-2 rounded-lg flex items-start justify-between text-xs transition-colors ${
                            isSelected 
                              ? 'bg-cyan-500/10 border border-cyan-500/30 text-cyan-200' 
                              : 'hover:bg-slate-800/50 text-slate-300'
                          }`}
                        >
                          <div className="min-w-0 pr-2">
                            <div className="font-mono font-medium flex items-center space-x-1.5">
                              <Wrench className="w-3.5 h-3.5 text-cyan-400 shrink-0" />
                              <span className="truncate">{tool.name}</span>
                            </div>
                            {tool.description && (
                              <p className="text-[11px] text-slate-400 truncate mt-0.5 font-sans">
                                {tool.description}
                              </p>
                            )}
                          </div>
                          <ChevronRight className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${isSelected ? 'text-cyan-400' : 'text-slate-600'}`} />
                        </button>
                      );
                    })
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Right Column: Tool Schema & Execution Playground */}
          <div className="lg:col-span-7 flex flex-col space-y-4">
            {selectedTool ? (
              <div className="border border-slate-800 rounded-xl p-4 bg-slate-950/60 space-y-4">
                <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                  <div>
                    <div className="flex items-center space-x-2">
                      <span className="text-xs text-slate-400 font-mono">{selectedTool.server} /</span>
                      <h4 className="font-mono font-bold text-slate-100 text-sm text-cyan-300">
                        {selectedTool.tool.name}
                      </h4>
                    </div>
                    {selectedTool.tool.description && (
                      <p className="text-xs text-slate-400 mt-1 font-sans">{selectedTool.tool.description}</p>
                    )}
                  </div>

                  <button
                    onClick={handleExecute}
                    disabled={executing}
                    className="flex items-center space-x-1.5 px-3.5 py-1.5 bg-cyan-600 hover:bg-cyan-500 text-white rounded-lg text-xs font-semibold shadow-md transition-colors disabled:opacity-50 font-mono"
                  >
                    {executing ? (
                      <>
                        <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                        <span>Calling...</span>
                      </>
                    ) : (
                      <>
                        <Play className="w-3.5 h-3.5 fill-current" />
                        <span>Test Execute</span>
                      </>
                    )}
                  </button>
                </div>

                {/* Arguments JSON Input */}
                <div className="space-y-1.5">
                  <label className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider font-mono flex items-center justify-between">
                    <span>Arguments (JSON)</span>
                    <span className="text-slate-500 font-normal">Valid JSON payload</span>
                  </label>
                  <textarea
                    value={toolArgs}
                    onChange={e => setToolArgs(e.target.value)}
                    rows={5}
                    className="w-full bg-slate-900 text-slate-100 font-mono text-xs p-3 rounded-lg border border-slate-700/70 focus:outline-none focus:border-cyan-500 resize-y"
                    placeholder="{}"
                  />
                </div>

                {/* Parameters Schema View */}
                {(selectedTool.tool.parameters || (selectedTool.tool as any).input_schema) && (
                  <div className="space-y-1.5">
                    <label className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider font-mono">
                      Declared JSON Schema
                    </label>
                    <pre className="bg-slate-900/60 text-slate-300 p-2.5 rounded-lg border border-slate-800 text-[11px] font-mono overflow-x-auto max-h-36">
                      {JSON.stringify(selectedTool.tool.parameters || (selectedTool.tool as any).input_schema, null, 2)}
                    </pre>
                  </div>
                )}

                {/* Execution Output */}
                {execResult && (
                  <div className="space-y-1.5 pt-2 border-t border-slate-800">
                    <label className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider font-mono flex items-center space-x-2">
                      <Sparkles className="w-3 h-3 text-cyan-400" />
                      <span>Response Output</span>
                    </label>
                    <pre className="bg-slate-900 text-slate-200 p-3 rounded-lg border border-slate-800 text-xs font-mono overflow-x-auto max-h-48 whitespace-pre-wrap">
                      {JSON.stringify(execResult, null, 2)}
                    </pre>
                  </div>
                )}
              </div>
            ) : (
              <div className="h-full border border-dashed border-slate-800 rounded-xl flex flex-col items-center justify-center p-8 text-slate-500 space-y-2 font-mono">
                <Wrench className="w-8 h-8 stroke-1 text-slate-600" />
                <p className="text-xs font-medium">Select a tool from the list to view schema and test run.</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
