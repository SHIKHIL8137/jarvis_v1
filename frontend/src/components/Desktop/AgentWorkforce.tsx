import { useEffect, useState, useCallback } from 'react';
import {
  Users,
  Shield,
  Search,
  Code2,
  FileSearch,
  CheckCircle2,
  Activity,
  RefreshCw,
  Power,
  ChevronRight,
  Send,
  AlertTriangle,
  Clock,
  Sparkles,
  Info,
  X,
  ListOrdered,
  FileCheck2,
} from 'lucide-react';
import {
  fetchWorkforceAgents,
  fetchTeamSummary,
  enableWorkforceAgent,
  disableWorkforceAgent,
  delegateTeamTask,
  fetchTaskTimeline,
  queryWorkforceStatus,
  type WorkforceAgent,
  type TeamSummary,
  type TaskTimeline,
} from '../../lib/api';

const AGENT_ICONS: Record<string, any> = {
  atlas: Users,
  oracle: Search,
  forge: Code2,
  sentinel: Shield,
  scout: FileSearch,
  vanguard: CheckCircle2,
  pulse: Activity,
};

const STATUS_BADGES: Record<string, { bg: string; text: string; label: string }> = {
  idle: { bg: 'rgba(34, 197, 94, 0.15)', text: '#4ade80', label: 'Idle / Ready' },
  busy: { bg: 'rgba(59, 130, 246, 0.15)', text: '#60a5fa', label: 'Busy' },
  online: { bg: 'rgba(34, 197, 94, 0.15)', text: '#4ade80', label: 'Online' },
  waiting_approval: { bg: 'rgba(234, 179, 8, 0.15)', text: '#facc15', label: 'Waiting Approval' },
  paused: { bg: 'rgba(156, 163, 175, 0.15)', text: '#9ca3af', label: 'Paused' },
  degraded: { bg: 'rgba(249, 115, 22, 0.15)', text: '#fb923c', label: 'Degraded' },
  offline: { bg: 'rgba(107, 114, 128, 0.15)', text: '#9ca3af', label: 'Offline' },
  failed: { bg: 'rgba(239, 68, 68, 0.15)', text: '#f87171', label: 'Failed' },
};

export function AgentWorkforce() {
  const [agents, setAgents] = useState<WorkforceAgent[]>([]);
  const [summary, setSummary] = useState<TeamSummary | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState<WorkforceAgent | null>(null);
  const [delegatingModal, setDelegatingModal] = useState(false);
  const [goalText, setGoalText] = useState('');
  const [priority, setPriority] = useState('normal');
  const [delegatingSubmitting, setDelegatingSubmitting] = useState(false);
  const [lastDelegation, setLastDelegation] = useState<any | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [timeline, setTimeline] = useState<TaskTimeline | null>(null);
  const [statusQuery, setStatusQuery] = useState('');
  const [statusAnswer, setStatusAnswer] = useState<string | null>(null);
  const [querying, setQuerying] = useState(false);

  const loadWorkforce = useCallback(async () => {
    setLoading(true);
    try {
      const [agentsData, summaryData] = await Promise.all([
        fetchWorkforceAgents(),
        fetchTeamSummary(),
      ]);
      setAgents(agentsData);
      setSummary(summaryData);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadWorkforce();
    const interval = setInterval(loadWorkforce, 5000);
    return () => clearInterval(interval);
  }, [loadWorkforce]);

  useEffect(() => {
    if (!selectedTaskId) return;
    fetchTaskTimeline(selectedTaskId).then(setTimeline).catch(() => {});
    const interval = setInterval(() => {
      fetchTaskTimeline(selectedTaskId).then(setTimeline).catch(() => {});
    }, 4000);
    return () => clearInterval(interval);
  }, [selectedTaskId]);

  const handleToggleAgent = async (agent: WorkforceAgent) => {
    if (agent.enabled) {
      await disableWorkforceAgent(agent.id);
    } else {
      await enableWorkforceAgent(agent.id);
    }
    loadWorkforce();
  };

  const handleDelegateSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!goalText.trim()) return;
    setDelegatingSubmitting(true);
    try {
      const result = await delegateTeamTask(goalText.trim(), priority);
      setLastDelegation(result);
      if (result.task_id) {
        setSelectedTaskId(result.task_id);
      }
      setGoalText('');
      setDelegatingModal(false);
      loadWorkforce();
    } catch (err: any) {
      alert(`Delegation error: ${err.message}`);
    } finally {
      setDelegatingSubmitting(false);
    }
  };

  const handleStatusQuery = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!statusQuery.trim()) return;
    setQuerying(true);
    try {
      const ans = await queryWorkforceStatus(statusQuery.trim());
      setStatusAnswer(ans);
    } finally {
      setQuerying(false);
    }
  };

  return (
    <div
      className="p-5 rounded-xl mb-6 shadow-xs"
      style={{
        background: 'var(--color-bg-secondary)',
        border: '1px solid var(--color-border)',
      }}
    >
      {/* Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 mb-4">
        <div>
          <div className="flex items-center gap-2">
            <Sparkles size={18} style={{ color: 'var(--color-accent)' }} />
            <h2 className="text-base font-bold" style={{ color: 'var(--color-text)' }}>
              Agent Workforce (Avengers Roster)
            </h2>
          </div>
          <p className="text-xs mt-0.5" style={{ color: 'var(--color-text-secondary)' }}>
            7 specialized autonomous agents led by ATLAS (Chief of Staff) executing DAG workflows with verification.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <button
            onClick={() => setDelegatingModal(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg text-white transition-opacity"
            style={{ background: 'var(--color-accent)' }}
          >
            <Send size={13} />
            Delegate Team Task
          </button>
          <button
            onClick={loadWorkforce}
            disabled={loading}
            className="p-1.5 rounded-lg border text-xs transition-opacity hover:opacity-80"
            style={{
              borderColor: 'var(--color-border)',
              color: 'var(--color-text-secondary)',
            }}
            title="Refresh workforce"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Summary Metrics Bar */}
      {summary && (
        <div
          className="grid grid-cols-2 sm:grid-cols-5 gap-2.5 p-3 rounded-lg mb-4 text-center text-xs"
          style={{ background: 'var(--color-bg-tertiary)' }}
        >
          <div>
            <div className="text-[10px] uppercase font-semibold" style={{ color: 'var(--color-text-tertiary)' }}>
              Total Agents
            </div>
            <div className="text-sm font-bold mt-0.5" style={{ color: 'var(--color-text)' }}>
              {summary.total_registered}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase font-semibold" style={{ color: '#4ade80' }}>
              Online / Idle
            </div>
            <div className="text-sm font-bold mt-0.5" style={{ color: '#4ade80' }}>
              {summary.total_idle}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase font-semibold" style={{ color: '#60a5fa' }}>
              Active Tasks
            </div>
            <div className="text-sm font-bold mt-0.5" style={{ color: '#60a5fa' }}>
              {summary.total_busy}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase font-semibold" style={{ color: '#fb923c' }}>
              Degraded
            </div>
            <div className="text-sm font-bold mt-0.5" style={{ color: '#fb923c' }}>
              {summary.total_degraded}
            </div>
          </div>
          <div>
            <div className="text-[10px] uppercase font-semibold" style={{ color: '#9ca3af' }}>
              Offline
            </div>
            <div className="text-sm font-bold mt-0.5" style={{ color: '#9ca3af' }}>
              {summary.total_offline}
            </div>
          </div>
        </div>
      )}

      {/* Natural Language Query Bar */}
      <form onSubmit={handleStatusQuery} className="flex gap-2 mb-4">
        <input
          type="text"
          value={statusQuery}
          onChange={(e) => setStatusQuery(e.target.value)}
          placeholder='Ask status (e.g. "How many agents online?", "What is Forge doing?")'
          className="flex-1 px-3 py-1.5 text-xs rounded-lg border outline-none"
          style={{
            background: 'var(--color-bg)',
            borderColor: 'var(--color-border)',
            color: 'var(--color-text)',
          }}
        />
        <button
          type="submit"
          disabled={querying || !statusQuery.trim()}
          className="px-3 py-1.5 text-xs font-medium rounded-lg border transition-opacity hover:opacity-80"
          style={{
            borderColor: 'var(--color-border)',
            color: 'var(--color-text)',
          }}
        >
          {querying ? 'Asking...' : 'Ask Jarvis'}
        </button>
      </form>

      {statusAnswer && (
        <div
          className="p-3 rounded-lg mb-4 text-xs flex items-start justify-between gap-2"
          style={{ background: 'rgba(59, 130, 246, 0.1)', color: '#60a5fa', border: '1px solid rgba(59, 130, 246, 0.2)' }}
        >
          <div className="flex items-start gap-2">
            <Sparkles size={14} className="mt-0.5 shrink-0" />
            <div>{statusAnswer}</div>
          </div>
          <button onClick={() => setStatusAnswer(null)} className="text-xs hover:opacity-75">
            <X size={12} />
          </button>
        </div>
      )}

      {/* Specialist Agent Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3">
        {agents.map((agent) => {
          const IconComponent = AGENT_ICONS[agent.id] || Users;
          const badge = STATUS_BADGES[agent.status] || STATUS_BADGES.offline;

          return (
            <div
              key={agent.id}
              className="p-3.5 rounded-lg border flex flex-col justify-between transition-all hover:shadow-xs"
              style={{
                background: 'var(--color-bg)',
                borderColor: selectedAgent?.id === agent.id ? 'var(--color-accent)' : 'var(--color-border)',
              }}
            >
              <div>
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div className="flex items-center gap-2">
                    <div
                      className="p-2 rounded-lg text-sm"
                      style={{
                        background: 'var(--color-bg-secondary)',
                        color: 'var(--color-accent)',
                      }}
                    >
                      <IconComponent size={16} />
                    </div>
                    <div>
                      <div className="text-xs font-bold leading-tight" style={{ color: 'var(--color-text)' }}>
                        {agent.display_name}
                      </div>
                      <div className="text-[10px]" style={{ color: 'var(--color-text-tertiary)' }}>
                        {agent.role.split('—')[1]?.trim() || agent.role}
                      </div>
                    </div>
                  </div>

                  <span
                    className="px-2 py-0.5 rounded text-[10px] font-semibold shrink-0"
                    style={{ background: badge.bg, color: badge.text }}
                  >
                    {badge.label}
                  </span>
                </div>

                <p className="text-[11px] line-clamp-2 mb-3" style={{ color: 'var(--color-text-secondary)' }}>
                  {agent.description}
                </p>
              </div>

              <div>
                <div className="flex items-center justify-between text-[10px] pt-2 border-t mb-2" style={{ borderColor: 'var(--color-border)' }}>
                  <span className="font-mono" style={{ color: 'var(--color-text-tertiary)' }}>
                    {agent.active_model}
                  </span>
                  <span style={{ color: agent.runtime_health === 'healthy' ? '#4ade80' : '#fb923c' }}>
                    &bull; {agent.runtime_health}
                  </span>
                </div>

                <div className="flex items-center justify-between gap-2">
                  <button
                    onClick={() => setSelectedAgent(agent)}
                    className="flex-1 text-center py-1 rounded text-[10px] font-medium border transition-opacity hover:opacity-80"
                    style={{ borderColor: 'var(--color-border)', color: 'var(--color-text)' }}
                  >
                    Details
                  </button>
                  <button
                    onClick={() => handleToggleAgent(agent)}
                    className="p-1 rounded border transition-opacity hover:opacity-80"
                    style={{
                      borderColor: 'var(--color-border)',
                      color: agent.enabled ? '#4ade80' : '#9ca3af',
                    }}
                    title={agent.enabled ? 'Disable agent' : 'Enable agent'}
                  >
                    <Power size={12} />
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Agent Details Modal/Drawer */}
      {selectedAgent && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div
            className="w-full max-w-lg p-5 rounded-xl border shadow-xl flex flex-col max-h-[85vh] overflow-y-auto"
            style={{
              background: 'var(--color-bg)',
              borderColor: 'var(--color-border)',
            }}
          >
            <div className="flex items-center justify-between pb-3 border-b mb-4" style={{ borderColor: 'var(--color-border)' }}>
              <div className="flex items-center gap-2">
                <span className="text-xl">{selectedAgent.avatar}</span>
                <div>
                  <h3 className="text-sm font-bold" style={{ color: 'var(--color-text)' }}>
                    {selectedAgent.display_name} — Manifest &amp; Security Profile
                  </h3>
                  <div className="text-xs" style={{ color: 'var(--color-text-tertiary)' }}>
                    {selectedAgent.role}
                  </div>
                </div>
              </div>
              <button onClick={() => setSelectedAgent(null)} className="p-1 text-xs hover:opacity-75">
                <X size={16} />
              </button>
            </div>

            <div className="space-y-3 text-xs">
              <div>
                <span className="font-semibold" style={{ color: 'var(--color-text)' }}>Description: </span>
                <span style={{ color: 'var(--color-text-secondary)' }}>{selectedAgent.description}</span>
              </div>
              <div>
                <span className="font-semibold" style={{ color: 'var(--color-text)' }}>Status: </span>
                <span className="uppercase font-mono font-bold" style={{ color: STATUS_BADGES[selectedAgent.status]?.text }}>
                  {selectedAgent.status}
                </span>
              </div>
              <div>
                <span className="font-semibold" style={{ color: 'var(--color-text)' }}>Assigned Model: </span>
                <span className="font-mono" style={{ color: 'var(--color-text-secondary)' }}>{selectedAgent.active_model}</span>
              </div>
              <div>
                <span className="font-semibold" style={{ color: 'var(--color-text)' }}>Health: </span>
                <span style={{ color: selectedAgent.runtime_health === 'healthy' ? '#4ade80' : '#fb923c' }}>
                  {selectedAgent.runtime_health}
                </span>
                {selectedAgent.last_error && (
                  <div className="text-[11px] text-red-400 mt-1">Error: {selectedAgent.last_error}</div>
                )}
              </div>

              <div>
                <div className="font-semibold mb-1" style={{ color: 'var(--color-text)' }}>
                  Allowed Tools ({selectedAgent.allowed_tools.length}):
                </div>
                <div className="flex flex-wrap gap-1">
                  {selectedAgent.allowed_tools.map((t) => (
                    <span
                      key={t}
                      className="px-2 py-0.5 rounded text-[10px] font-mono"
                      style={{ background: 'var(--color-bg-secondary)', border: '1px solid var(--color-border)' }}
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>

              {selectedAgent.denied_tools.length > 0 && (
                <div>
                  <div className="font-semibold mb-1 text-red-400">
                    Explicitly Denied Tools ({selectedAgent.denied_tools.length}):
                  </div>
                  <div className="flex flex-wrap gap-1">
                    {selectedAgent.denied_tools.map((t) => (
                      <span
                        key={t}
                        className="px-2 py-0.5 rounded text-[10px] font-mono bg-red-500/10 text-red-400 border border-red-500/20"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="mt-5 pt-3 border-t flex justify-end" style={{ borderColor: 'var(--color-border)' }}>
              <button
                onClick={() => setSelectedAgent(null)}
                className="px-4 py-1.5 text-xs font-medium rounded-lg border"
                style={{ borderColor: 'var(--color-border)', color: 'var(--color-text)' }}
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Multi-Agent Delegation Modal */}
      {delegatingModal && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div
            className="w-full max-w-md p-5 rounded-xl border shadow-xl flex flex-col"
            style={{
              background: 'var(--color-bg)',
              borderColor: 'var(--color-border)',
            }}
          >
            <div className="flex items-center justify-between pb-3 border-b mb-4" style={{ borderColor: 'var(--color-border)' }}>
              <div className="flex items-center gap-2">
                <Sparkles size={16} style={{ color: 'var(--color-accent)' }} />
                <h3 className="text-sm font-bold" style={{ color: 'var(--color-text)' }}>
                  Delegate Multi-Agent Task
                </h3>
              </div>
              <button onClick={() => setDelegatingModal(false)} className="p-1 text-xs hover:opacity-75">
                <X size={16} />
              </button>
            </div>

            <form onSubmit={handleDelegateSubmit} className="space-y-3">
              <div>
                <label className="block text-xs font-semibold mb-1" style={{ color: 'var(--color-text)' }}>
                  Mission Goal / User Request
                </label>
                <textarea
                  rows={3}
                  required
                  value={goalText}
                  onChange={(e) => setGoalText(e.target.value)}
                  placeholder="e.g. Analyze ERP project, identify bugs, check security, and recommend improvements"
                  className="w-full px-3 py-2 text-xs rounded-lg border outline-none"
                  style={{
                    background: 'var(--color-bg-secondary)',
                    borderColor: 'var(--color-border)',
                    color: 'var(--color-text)',
                  }}
                />
              </div>

              <div>
                <label className="block text-xs font-semibold mb-1" style={{ color: 'var(--color-text)' }}>
                  Priority
                </label>
                <select
                  value={priority}
                  onChange={(e) => setPriority(e.target.value)}
                  className="w-full px-3 py-1.5 text-xs rounded-lg border outline-none"
                  style={{
                    background: 'var(--color-bg-secondary)',
                    borderColor: 'var(--color-border)',
                    color: 'var(--color-text)',
                  }}
                >
                  <option value="low">Low</option>
                  <option value="normal">Normal</option>
                  <option value="high">High</option>
                  <option value="critical">Critical</option>
                </select>
              </div>

              <div className="text-[11px]" style={{ color: 'var(--color-text-tertiary)' }}>
                ATLAS will decompose this goal into a DAG with specialized assignments (Scout, Forge, Sentinel, Oracle, Vanguard).
              </div>

              <div className="pt-2 flex justify-end gap-2">
                <button
                  type="button"
                  onClick={() => setDelegatingModal(false)}
                  className="px-3 py-1.5 text-xs rounded-lg border"
                  style={{ borderColor: 'var(--color-border)', color: 'var(--color-text)' }}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={delegatingSubmitting || !goalText.trim()}
                  className="px-4 py-1.5 text-xs font-medium rounded-lg text-white"
                  style={{ background: 'var(--color-accent)' }}
                >
                  {delegatingSubmitting ? 'Planning...' : 'Launch Workforce'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Live Timeline & Execution Drawer if task selected */}
      {selectedTaskId && timeline && (
        <div
          className="mt-5 p-4 rounded-lg border"
          style={{
            background: 'var(--color-bg-tertiary)',
            borderColor: 'var(--color-border)',
          }}
        >
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <ListOrdered size={16} style={{ color: 'var(--color-accent)' }} />
              <h4 className="text-xs font-bold" style={{ color: 'var(--color-text)' }}>
                Multi-Agent Execution Timeline — Task {selectedTaskId.slice(0, 8)}
              </h4>
            </div>
            <button
              onClick={() => setSelectedTaskId(null)}
              className="text-xs hover:opacity-75"
              style={{ color: 'var(--color-text-tertiary)' }}
            >
              Close Timeline
            </button>
          </div>

          <div className="space-y-2 text-xs">
            {timeline.reports.map((report) => (
              <div
                key={report.id}
                className="p-3 rounded-lg border"
                style={{
                  background: 'var(--color-bg)',
                  borderColor: 'var(--color-border)',
                }}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="font-bold text-[11px] uppercase" style={{ color: 'var(--color-accent)' }}>
                    [{report.agent_id.toUpperCase()}] {report.title}
                  </span>
                  <span className="text-[10px] text-green-400 font-medium">
                    Verified (Confidence: {Math.round(report.confidence_score * 100)}%)
                  </span>
                </div>
                <p className="text-[11px] mb-2" style={{ color: 'var(--color-text-secondary)' }}>
                  {report.summary}
                </p>
                {report.key_findings && report.key_findings.length > 0 && (
                  <ul className="list-disc pl-4 text-[10px] space-y-0.5" style={{ color: 'var(--color-text-tertiary)' }}>
                    {report.key_findings.map((f, i) => (
                      <li key={i}>{f}</li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
