import React, { useState, useEffect } from 'react';
import { orchestratorApi } from '../api/client';
import {
  Settings as SettingsIcon,
  Save,
  Key,
  Globe,
  Cpu,
  Server,
  Terminal,
  Shield,
  CheckCircle2,
  AlertCircle
} from 'lucide-react';

interface SettingsPageProps {
  navigate: (route: string) => void;
}

export const SettingsPage: React.FC<SettingsPageProps> = () => {
  const [gatewayUrl, setGatewayUrl] = useState('http://127.0.0.1:8000');
  const [apiKey, setApiKey] = useState('sk-••••••••••••••••••••••••');
  const [fallbackEndpoint, setFallbackEndpoint] = useState('https://api.anthropic.com/v1');
  const [fastModel, setFastModel] = useState('claude-3-5-haiku');
  const [balancedModel, setBalancedModel] = useState('claude-3-7-sonnet');
  const [frontierModel, setFrontierModel] = useState('claude-3-7-sonnet');
  const [maxRetries, setMaxRetries] = useState(3);
  const [timeoutSeconds, setTimeoutSeconds] = useState(120);
  const [logLevel, setLogLevel] = useState('INFO');
  const [savedSuccess, setSavedSuccess] = useState(false);

  useEffect(() => {
    orchestratorApi.getSettings().then((s) => {
      if (s.gateway_url) setGatewayUrl(s.gateway_url);
      if (s.fast_model) setFastModel(s.fast_model);
      if (s.balanced_model) setBalancedModel(s.balanced_model);
      if (s.frontier_model) setFrontierModel(s.frontier_model);
    }).catch(console.error);
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      await orchestratorApi.saveSettings({
        gateway_url: gatewayUrl,
        fallback_endpoint: fallbackEndpoint,
        fast_model: fastModel,
        balanced_model: balancedModel,
        frontier_model: frontierModel,
        max_retries: maxRetries,
        timeout_seconds: timeoutSeconds,
        log_level: logLevel,
      });
      setSavedSuccess(true);
      setTimeout(() => setSavedSuccess(false), 3000);
    } catch (err: any) {
      alert(`Save failed: ${err.message}`);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold font-sans text-content-primary tracking-tight flex items-center gap-2.5">
            <SettingsIcon className="w-6 h-6 text-accent-primary" />
            System & Gateway Configuration
          </h1>
          <p className="text-xs font-mono text-content-secondary mt-1">
            Global inference gateway, API keys, fallback routes, timeouts, and MCP tool discovery
          </p>
        </div>

        {savedSuccess && (
          <div className="flex items-center gap-1.5 bg-emerald-950 text-emerald-400 border border-emerald-500/30 px-3 py-1.5 rounded-xl text-xs font-mono font-bold animate-in fade-in">
            <CheckCircle2 className="w-4 h-4" />
            Configuration Saved
          </div>
        )}
      </div>

      <form onSubmit={handleSave} className="space-y-6">
        {/* Gateway & Auth */}
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-6 shadow-xl space-y-4">
          <div className="flex items-center gap-2 pb-2 border-b border-border-subtle">
            <Globe className="w-4 h-4 text-accent-primary" />
            <h3 className="text-xs font-mono font-bold text-content-primary uppercase">
              Inference Gateway & Vault
            </h3>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-mono text-content-muted mb-1.5">GATEWAY BASE URL</label>
              <input
                type="text"
                value={gatewayUrl}
                onChange={(e) => setGatewayUrl(e.target.value)}
                className="w-full bg-bg-base border border-border-subtle rounded-lg px-3 py-2 text-xs font-mono text-content-primary focus:outline-none focus:border-accent-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-mono text-content-muted mb-1.5">API KEY (MASKED)</label>
              <div className="relative">
                <input
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  className="w-full bg-bg-base border border-border-subtle rounded-lg px-3 py-2 text-xs font-mono text-content-primary focus:outline-none focus:border-accent-primary"
                />
                <Key className="w-3.5 h-3.5 text-content-muted absolute right-3 top-2.5" />
              </div>
            </div>
          </div>
        </div>

        {/* Model Tiers */}
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-6 shadow-xl space-y-4">
          <div className="flex items-center gap-2 pb-2 border-b border-border-subtle">
            <Cpu className="w-4 h-4 text-accent-info" />
            <h3 className="text-xs font-mono font-bold text-content-primary uppercase">
              Model Tier Mapping
            </h3>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-mono text-content-muted mb-1.5">FAST TIER</label>
              <input
                type="text"
                value={fastModel}
                onChange={(e) => setFastModel(e.target.value)}
                className="w-full bg-bg-base border border-border-subtle rounded-lg px-3 py-2 text-xs font-mono text-content-primary focus:outline-none focus:border-accent-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-mono text-content-muted mb-1.5">BALANCED TIER</label>
              <input
                type="text"
                value={balancedModel}
                onChange={(e) => setBalancedModel(e.target.value)}
                className="w-full bg-bg-base border border-border-subtle rounded-lg px-3 py-2 text-xs font-mono text-content-primary focus:outline-none focus:border-accent-primary"
              />
            </div>
            <div>
              <label className="block text-xs font-mono text-content-muted mb-1.5">FRONTIER TIER</label>
              <input
                type="text"
                value={frontierModel}
                onChange={(e) => setFrontierModel(e.target.value)}
                className="w-full bg-bg-base border border-border-subtle rounded-lg px-3 py-2 text-xs font-mono text-content-primary focus:outline-none focus:border-accent-primary"
              />
            </div>
          </div>
        </div>

        {/* Execution Policies */}
        <div className="bg-bg-panel border border-border-subtle rounded-xl p-6 shadow-xl space-y-4">
          <div className="flex items-center gap-2 pb-2 border-b border-border-subtle">
            <Shield className="w-4 h-4 text-accent-warning" />
            <h3 className="text-xs font-mono font-bold text-content-primary uppercase">
              Fault Tolerance & Logging
            </h3>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-mono text-content-muted mb-1.5">MAX RETRIES</label>
              <input
                type="number"
                value={maxRetries}
                onChange={(e) => setMaxRetries(Number(e.target.value))}
                className="w-full bg-bg-base border border-border-subtle rounded-lg px-3 py-2 text-xs font-mono text-content-primary focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-mono text-content-muted mb-1.5">TIMEOUT (SECONDS)</label>
              <input
                type="number"
                value={timeoutSeconds}
                onChange={(e) => setTimeoutSeconds(Number(e.target.value))}
                className="w-full bg-bg-base border border-border-subtle rounded-lg px-3 py-2 text-xs font-mono text-content-primary focus:outline-none"
              />
            </div>
            <div>
              <label className="block text-xs font-mono text-content-muted mb-1.5">LOG LEVEL</label>
              <select
                value={logLevel}
                onChange={(e) => setLogLevel(e.target.value)}
                className="w-full bg-bg-base border border-border-subtle rounded-lg px-3 py-2 text-xs font-mono text-content-primary focus:outline-none"
              >
                <option value="DEBUG">DEBUG</option>
                <option value="INFO">INFO</option>
                <option value="WARNING">WARNING</option>
                <option value="ERROR">ERROR</option>
              </select>
            </div>
          </div>
        </div>

        {/* Save Button */}
        <div className="flex justify-end">
          <button
            type="submit"
            className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-accent-primary hover:bg-indigo-600 text-white font-mono text-xs font-bold shadow-lg shadow-accent-primary/25 transition-all"
          >
            <Save className="w-4 h-4" />
            Save Configuration
          </button>
        </div>
      </form>
    </div>
  );
};
