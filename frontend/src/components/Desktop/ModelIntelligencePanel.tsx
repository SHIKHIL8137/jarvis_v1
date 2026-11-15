import React, { useEffect, useState, useCallback } from 'react';
import {
  Cpu,
  Layers,
  Sparkles,
  RefreshCw,
  Sliders,
  CheckCircle2,
  AlertCircle,
  HelpCircle,
  HardDrive,
  Eye,
  Terminal,
  Brain,
  Wrench,
} from 'lucide-react';
import {
  fetchDiscoveredModels,
  fetchModelProviders,
  fetchModelPolicy,
  updateModelPolicy,
  explainModelSelection,
  fetchHardwareProfile,
  type DiscoveredModel,
  type ModelProviderStatus,
  type ModelPolicyResponse,
  type ModelSelectionExplanation,
  type HardwareProfile,
} from '../../lib/api';

export function ModelIntelligencePanel() {
  const [models, setModels] = useState<DiscoveredModel[]>([]);
  const [totalCount, setTotalCount] = useState(0);
  const [localCount, setLocalCount] = useState(0);
  const [cloudCount, setCloudCount] = useState(0);
  const [providers, setProviders] = useState<ModelProviderStatus[]>([]);
  const [policyData, setPolicyData] = useState<ModelPolicyResponse | null>(null);
  const [hardware, setHardware] = useState<HardwareProfile | null>(null);
  const [loading, setLoading] = useState(false);
  const [updatingPolicy, setUpdatingPolicy] = useState(false);
  const [selectedTaskType, setSelectedTaskType] = useState('coding');
  const [explanation, setExplanation] = useState<ModelSelectionExplanation | null>(null);
  const [explaining, setExplaining] = useState(false);

  const loadData = useCallback(async (refresh: boolean = false) => {
    setLoading(true);
    try {
      const [disc, prov, pol, hw] = await Promise.all([
        fetchDiscoveredModels(refresh),
        fetchModelProviders(),
        fetchModelPolicy(),
        fetchHardwareProfile(),
      ]);
      setModels(disc.models || []);
      setTotalCount(disc.total || 0);
      setLocalCount(disc.local_count || 0);
      setCloudCount(disc.cloud_count || 0);
      setProviders(prov || []);
      setPolicyData(pol);
      setHardware(hw);
    } catch (err) {
      console.error('Failed to load model intelligence data:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData(false);
  }, [loadData]);

  const handlePolicyChange = async (newPolicy: string) => {
    setUpdatingPolicy(true);
    try {
      const updated = await updateModelPolicy(newPolicy);
      if (policyData) {
        setPolicyData({ ...policyData, active_policy: updated });
      }
    } catch (err) {
      console.error('Failed to update policy:', err);
    } finally {
      setUpdatingPolicy(false);
    }
  };

  const handleExplain = async () => {
    setExplaining(true);
    try {
      const exp = await explainModelSelection(selectedTaskType);
      setExplanation(exp);
    } catch (err) {
      console.error('Failed to explain model selection:', err);
    } finally {
      setExplaining(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Top Controls: Policy & Hardware Profile */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Hardware Governance Card */}
        <div
          className="p-4 rounded-xl border"
          style={{
            background: 'var(--color-bg)',
            borderColor: 'var(--color-border)',
          }}
        >
          <div className="flex items-center gap-2 mb-3">
            <Cpu size={18} style={{ color: 'var(--color-accent)' }} />
            <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--color-text)' }}>
              Hardware & VRAM Governance
            </h3>
          </div>
          <div className="grid grid-cols-2 gap-2 text-xs">
            <div className="p-2.5 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
              <div className="text-[10px] text-zinc-400 font-medium">GPU & VRAM Boundary</div>
              <div className="font-bold text-zinc-200 mt-0.5">
                {hardware?.gpu_detected ?? 'NVIDIA GTX 1650 (4.0 GB)'}
              </div>
            </div>
            <div className="p-2.5 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
              <div className="text-[10px] text-zinc-400 font-medium">Concurrency Limit</div>
              <div className="font-bold text-zinc-200 mt-0.5">
                Max {hardware?.max_concurrent_inferences ?? 2} Local Inferences
              </div>
            </div>
            <div className="p-2.5 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
              <div className="text-[10px] text-zinc-400 font-medium">Host CPU</div>
              <div className="font-bold text-zinc-200 mt-0.5">
                {hardware?.cpu_profile ?? 'AMD Ryzen 5 5600H'}
              </div>
            </div>
            <div className="p-2.5 rounded-lg" style={{ background: 'var(--color-bg-tertiary)' }}>
              <div className="text-[10px] text-zinc-400 font-medium">Active Allocations</div>
              <div className="font-bold text-emerald-400 mt-0.5">
                {hardware?.active_inferences ?? 0} in progress
              </div>
            </div>
          </div>
        </div>

        {/* Dynamic Allocation Policy Card */}
        <div
          className="p-4 rounded-xl border"
          style={{
            background: 'var(--color-bg)',
            borderColor: 'var(--color-border)',
          }}
        >
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <Sliders size={18} style={{ color: 'var(--color-accent)' }} />
              <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--color-text)' }}>
                Dynamic Allocation Policy
              </h3>
            </div>
            {updatingPolicy && <RefreshCw size={13} className="animate-spin text-zinc-400" />}
          </div>
          <p className="text-xs text-zinc-400 mb-3">
            Determines how the workforce routes tasks between low-VRAM local models and capable cloud endpoints.
          </p>
          <div className="flex flex-wrap gap-2">
            {policyData?.available_policies?.map((pol) => {
              const active = policyData.active_policy === pol;
              return (
                <button
                  key={pol}
                  onClick={() => handlePolicyChange(pol)}
                  disabled={updatingPolicy}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                    active
                      ? 'bg-blue-600 text-white shadow-sm'
                      : 'bg-zinc-800 text-zinc-400 hover:text-zinc-200 hover:bg-zinc-700'
                  }`}
                >
                  {pol}
                </button>
              );
            }) || (
              <span className="text-xs text-zinc-500">Loading policies...</span>
            )}
          </div>
        </div>
      </div>

      {/* Dynamic Model Selection Explainer */}
      <div
        className="p-4 rounded-xl border"
        style={{
          background: 'var(--color-bg)',
          borderColor: 'var(--color-border)',
        }}
      >
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Brain size={18} style={{ color: '#a78bfa' }} />
            <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--color-text)' }}>
              Model Selection Explainer & Verification
            </h3>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={selectedTaskType}
              onChange={(e) => setSelectedTaskType(e.target.value)}
              className="px-2.5 py-1 text-xs rounded-lg border bg-zinc-900 text-zinc-200 border-zinc-700 outline-none"
            >
              <option value="coding">Task: Coding (Forge)</option>
              <option value="planning">Task: Planning (Atlas)</option>
              <option value="research">Task: Deep Research (Oracle)</option>
              <option value="security">Task: Security Audit (Sentinel)</option>
              <option value="qa_verification">Task: QA Verification (Vanguard)</option>
              <option value="operations">Task: Operations (Pulse)</option>
              <option value="chat">Task: Fast Conversational</option>
            </select>
            <button
              onClick={handleExplain}
              disabled={explaining}
              className="px-3 py-1 text-xs font-medium rounded-lg bg-purple-600 text-white hover:bg-purple-700 transition-colors"
            >
              {explaining ? 'Evaluating...' : 'Explain Selection'}
            </button>
          </div>
        </div>

        {explanation ? (
          <div className="p-3.5 rounded-lg bg-zinc-900 border border-zinc-800 space-y-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="text-zinc-400">Selected Model:</span>
              <span className="font-bold text-emerald-400 text-sm">
                {explanation.selected_model}{' '}
                <span className="text-[10px] text-zinc-500">({explanation.provider})</span>
              </span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-zinc-400">Capability Fit Score:</span>
              <span className="font-semibold text-zinc-200">{explanation.score.toFixed(2)} / 1.0</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-zinc-400">Policy & Constraints:</span>
              <span className="text-zinc-300 font-mono text-[11px]">{explanation.policy_used}</span>
            </div>
            <div className="pt-2 border-t border-zinc-800 text-zinc-300 leading-relaxed">
              <span className="font-semibold text-zinc-400">Rationale: </span>
              {explanation.rationale}
            </div>
          </div>
        ) : (
          <div className="text-xs text-zinc-500 italic py-2">
            Select a task type and click &quot;Explain Selection&quot; to inspect real-time capability scoring, hardware limits, and rationale.
          </div>
        )}
      </div>

      {/* Discovered Models Catalog */}
      <div
        className="p-4 rounded-xl border"
        style={{
          background: 'var(--color-bg)',
          borderColor: 'var(--color-border)',
        }}
      >
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Layers size={18} style={{ color: 'var(--color-accent)' }} />
            <h3 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--color-text)' }}>
              Discovered Models ({totalCount}) &bull; {localCount} Local / {cloudCount} Cloud
            </h3>
          </div>
          <button
            onClick={() => loadData(true)}
            disabled={loading}
            className="flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-800 transition-colors"
          >
            <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
            Re-discover
          </button>
        </div>

        <div className="overflow-x-auto max-h-96">
          <table className="w-full text-left text-xs text-zinc-300 border-collapse">
            <thead>
              <tr className="border-b border-zinc-800 text-[10px] uppercase text-zinc-400 bg-zinc-900/50">
                <th className="py-2 px-3">Model ID</th>
                <th className="py-2 px-3">Provider</th>
                <th className="py-2 px-3">Type</th>
                <th className="py-2 px-3">Capabilities</th>
                <th className="py-2 px-3">Context</th>
                <th className="py-2 px-3">Cost / 1M Out</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-zinc-800/50">
              {models.slice(0, 30).map((m) => (
                <tr key={m.id} className="hover:bg-zinc-800/30 transition-colors">
                  <td className="py-2.5 px-3 font-mono font-medium text-zinc-200">
                    {m.id}
                  </td>
                  <td className="py-2.5 px-3 text-zinc-400">{m.provider}</td>
                  <td className="py-2.5 px-3">
                    <span
                      className={`px-2 py-0.5 rounded text-[10px] font-semibold ${
                        m.is_local ? 'bg-emerald-950 text-emerald-400' : 'bg-blue-950 text-blue-400'
                      }`}
                    >
                      {m.is_local ? 'Local' : 'Cloud'}
                    </span>
                  </td>
                  <td className="py-2.5 px-3">
                    <div className="flex items-center gap-1">
                      {m.capabilities?.tool_calling && (
                        <span className="p-1 rounded bg-zinc-800 text-amber-400" title="Tool Calling">
                          <Wrench size={10} />
                        </span>
                      )}
                      {m.capabilities?.coding && (
                        <span className="p-1 rounded bg-zinc-800 text-blue-400" title="Code Generation">
                          <Terminal size={10} />
                        </span>
                      )}
                      {m.capabilities?.reasoning && (
                        <span className="p-1 rounded bg-zinc-800 text-purple-400" title="Reasoning">
                          <Brain size={10} />
                        </span>
                      )}
                      {m.capabilities?.vision && (
                        <span className="p-1 rounded bg-zinc-800 text-pink-400" title="Vision">
                          <Eye size={10} />
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="py-2.5 px-3 font-mono text-zinc-400">
                    {(m.context_tokens / 1024).toFixed(0)}k
                  </td>
                  <td className="py-2.5 px-3 text-zinc-400">
                    {m.cost_per_m_out === 0 ? 'Free (Local)' : `$${m.cost_per_m_out.toFixed(2)}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {totalCount > 30 && (
            <div className="p-2 text-center text-xs text-zinc-500 bg-zinc-900/40">
              Showing top 30 of {totalCount} discovered models.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
