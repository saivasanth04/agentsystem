import React, { useState, useEffect } from 'react';
import { gatewayApi } from '../api/gatewayApi';
import { GatewaySettings } from '../types/gateway';
import {
  Settings,
  Key,
  Copy,
  Check,
  Save,
  Clock,
  ShieldCheck,
  Database,
  Repeat,
  DollarSign,
  AlertCircle,
} from 'lucide-react';

interface GatewaySettingsPageProps {
  navigate: (route: string) => void;
}

export const GatewaySettingsPage: React.FC<GatewaySettingsPageProps> = () => {
  const [settings, setSettings] = useState<GatewaySettings | null>(null);
  const [copiedKey, setCopiedKey] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [saveSuccess, setSaveSuccess] = useState(false);
  const [intervalSec, setIntervalSec] = useState<number>(300);
  const [timeoutSec, setTimeoutSec] = useState<number>(60);
  const [maxRetries, setMaxRetries] = useState<number>(3);
  const [routingStrategy, setRoutingStrategy] = useState<string>('latency-based-routing');
  const [cacheEnabled, setCacheEnabled] = useState<boolean>(true);

  useEffect(() => {
    const fetchSettings = async () => {
      try {
        const data = await gatewayApi.getSettings();
        setSettings(data);
        setIntervalSec(data.discovery_interval_seconds);
        setTimeoutSec(data.request_timeout);
        setMaxRetries(data.max_retries);
        setRoutingStrategy(data.routing_strategy);
        setCacheEnabled(data.semantic_cache_enabled);
      } catch (err: any) {
        console.error('Failed to load gateway settings:', err);
      }
    };
    fetchSettings();
  }, []);

  const handleCopyKey = () => {
    if (settings?.unified_api_key) {
      navigator.clipboard.writeText(settings.unified_api_key);
      setCopiedKey(true);
      setTimeout(() => setCopiedKey(false), 2000);
    }
  };

  const handleSave = async () => {
    setIsSaving(true);
    setSaveSuccess(false);
    try {
      await gatewayApi.updateSettings({
        discovery_interval_seconds: intervalSec,
        request_timeout: timeoutSec,
        max_retries: maxRetries,
        routing_strategy: routingStrategy,
        semantic_cache_enabled: cacheEnabled,
      });
      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 3000);
    } catch (err: any) {
      alert(`Failed to save settings: ${err.message}`);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="p-6 max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center gap-3.5 p-5 bg-slate-900 border border-slate-800 rounded-2xl shadow-xl">
        <div className="w-11 h-11 rounded-xl bg-gradient-to-tr from-cyan-500 to-indigo-600 flex items-center justify-center shadow-lg shadow-cyan-500/20">
          <Settings className="w-6 h-6 text-white" />
        </div>
        <div>
          <h1 className="text-xl font-bold font-mono text-slate-100">
            Gateway Policy & Engine Configuration
          </h1>
          <p className="text-xs text-slate-400">
            Unified Virtual Key • LiteLLM Routing Engine • Background Discovery & Cache Parameters
          </p>
        </div>
      </div>

      {/* Unified Key Section */}
      <div className="p-5 bg-slate-900 border border-slate-800 rounded-2xl space-y-3">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Key className="w-4 h-4 text-cyan-400" />
            <h3 className="text-sm font-mono font-bold text-slate-200 uppercase">
              Unified API Key (Single Secret for Entire System)
            </h3>
          </div>
          <span className="text-[11px] font-mono text-emerald-400 font-semibold bg-emerald-950/30 px-2 py-0.5 rounded border border-emerald-800/40">
            SYSTEM ACTIVE
          </span>
        </div>

        <p className="text-xs text-slate-400">
          All client SDKs, background agents, and testing tools authenticate with this single virtual key. The gateway manages downstream provider credentials dynamically.
        </p>

        <div className="flex items-center gap-2 pt-2">
          <div className="flex-1 bg-slate-950 px-4 py-2.5 rounded-xl border border-slate-800 font-mono text-xs text-cyan-300 truncate select-all">
            {settings?.unified_api_key || 'sk-unified-loading...'}
          </div>

          <button
            onClick={handleCopyKey}
            className="flex items-center gap-1.5 px-4 py-2.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-xs font-mono font-medium text-slate-200 border border-slate-700 cursor-pointer transition-colors"
          >
            {copiedKey ? <Check className="w-4 h-4 text-emerald-400" /> : <Copy className="w-4 h-4" />}
            <span>{copiedKey ? 'Copied' : 'Copy Key'}</span>
          </button>
        </div>
      </div>

      {/* LiteLLM Routing Strategy */}
      <div className="p-5 bg-slate-900 border border-slate-800 rounded-2xl space-y-4">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-indigo-400" />
          <h3 className="text-sm font-mono font-bold text-slate-200 uppercase">
            LiteLLM Router Strategy & Resilience
          </h3>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="space-y-1.5">
            <label className="text-xs font-mono text-slate-300 font-semibold">
              Deployment Routing Strategy
            </label>
            <select
              value={routingStrategy}
              onChange={(e) => setRoutingStrategy(e.target.value)}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500 cursor-pointer"
            >
              <option value="latency-based-routing">latency-based-routing (LiteLLM Observed Lowest Latency)</option>
              <option value="least-busy">least-busy (Prefer Lowest Active Concurrency)</option>
              <option value="usage-based-routing-v2">usage-based-routing-v2 (Balanced RPM/TPM Distribution)</option>
              <option value="cost-based-routing">cost-based-routing (Prioritize Free & Low Cost)</option>
            </select>
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-mono text-slate-300 font-semibold">
              Max Request Retries
            </label>
            <input
              type="number"
              min={1}
              max={6}
              value={maxRetries}
              onChange={(e) => setMaxRetries(Number(e.target.value))}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
            />
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-2">
          <div className="space-y-1.5">
            <label className="text-xs font-mono text-slate-300 font-semibold">
              Background Model Discovery Interval (Seconds)
            </label>
            <input
              type="number"
              min={60}
              max={1800}
              value={intervalSec}
              onChange={(e) => setIntervalSec(Number(e.target.value))}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
            />
          </div>

          <div className="space-y-1.5">
            <label className="text-xs font-mono text-slate-300 font-semibold">
              Upstream Request Timeout (Seconds)
            </label>
            <input
              type="number"
              min={10}
              max={300}
              value={timeoutSec}
              onChange={(e) => setTimeoutSec(Number(e.target.value))}
              className="w-full bg-slate-950 border border-slate-800 rounded-xl p-2.5 text-xs font-mono text-slate-200 focus:outline-none focus:border-cyan-500"
            />
          </div>
        </div>
      </div>

      {/* Caching and Spend Policy */}
      <div className="p-5 bg-slate-900 border border-slate-800 rounded-2xl space-y-4">
        <div className="flex items-center gap-2">
          <Database className="w-4 h-4 text-emerald-400" />
          <h3 className="text-sm font-mono font-bold text-slate-200 uppercase">
            Semantic Caching & Spend Policy
          </h3>
        </div>

        <div className="flex items-center justify-between p-3.5 bg-slate-950 rounded-xl border border-slate-800">
          <div>
            <div className="text-xs font-mono font-bold text-slate-200">Semantic & Exact Response Caching</div>
            <div className="text-[11px] text-slate-400 mt-0.5">
              Caches deterministic repetitive agent requests (AST parsing, syntax validations) to eliminate redundant latency.
            </div>
          </div>
          <input
            type="checkbox"
            checked={cacheEnabled}
            onChange={(e) => setCacheEnabled(e.target.checked)}
            className="w-5 h-5 rounded accent-cyan-500 cursor-pointer"
          />
        </div>
      </div>

      {/* Save Button */}
      <div className="flex items-center justify-end gap-3 pt-2">
        {saveSuccess && (
          <span className="text-xs font-mono text-emerald-400 flex items-center gap-1">
            <Check className="w-3.5 h-3.5" />
            Settings saved successfully!
          </span>
        )}

        <button
          onClick={handleSave}
          disabled={isSaving}
          className="flex items-center gap-2 px-5 py-2.5 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-xs font-mono font-semibold text-white shadow-lg shadow-cyan-600/20 transition-all cursor-pointer disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          {isSaving ? 'Saving...' : 'Save Configuration'}
        </button>
      </div>
    </div>
  );
};
