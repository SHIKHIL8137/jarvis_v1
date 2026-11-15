import React, { useEffect, useState, useCallback } from 'react';
import {
  Workflow,
  CheckCircle2,
  XCircle,
  RefreshCw,
  Play,
  ShieldAlert,
  Clock,
  Tag,
  Send,
  ExternalLink,
} from 'lucide-react';
import {
  fetchN8nStatus,
  fetchN8nWorkflows,
  executeN8nWorkflow,
  type N8nStatus,
  type N8nWorkflowSummary,
} from '../../lib/api';

export function N8nPanel() {
  const [status, setStatus] = useState<N8nStatus | null>(null);
  const [workflows, setWorkflows] = useState<N8nWorkflowSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [executingId, setExecutingId] = useState<string | null>(null);
  const [payloadText, setPayloadText] = useState('{}');
  const [executionResult, setExecutionResult] = useState<any | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [st, wfs] = await Promise.all([
        fetchN8nStatus(),
        fetchN8nWorkflows(),
      ]);
      setStatus(st);
      setWorkflows(wfs);
    } catch (err) {
      console.error('Failed to load n8n data:', err);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 10000);
    return () => clearInterval(interval);
  }, [loadData]);

  const handleExecuteWorkflow = async (workflowId: string) => {
    setIsSubmitting(true);
    setExecutionResult(null);
    try {
      let data = {};
      try {
        data = JSON.parse(payloadText);
      } catch (e) {
        alert('Invalid JSON payload');
        setIsSubmitting(false);
        return;
      }
      const res = await executeN8nWorkflow(workflowId, data);
      setExecutionResult(res);
    } catch (err: any) {
      setExecutionResult({ error: err.message || 'Execution request failed' });
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Live Connectivity Banner */}
      <div
        className="p-4 rounded-xl border flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3"
        style={{
          background: 'var(--color-bg)',
          borderColor: 'var(--color-border)',
        }}
      >
        <div className="flex items-center gap-3">
          <div
            className={`w-9 h-9 rounded-lg flex items-center justify-center ${
              status?.online ? 'bg-emerald-950 text-emerald-400' : 'bg-red-950 text-red-400'
            }`}
          >
            <Workflow size={20} />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-bold" style={{ color: 'var(--color-text)' }}>
                n8n Workforce Connector
              </h3>
              <span
                className={`px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-wider ${
                  status?.online
                    ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                    : 'bg-red-950 text-red-400 border border-red-800'
                }`}
              >
                {status?.online ? 'Online' : 'Offline'}
              </span>
            </div>
            <p className="text-xs text-zinc-400 mt-0.5">
              Live Instance:{' '}
              <span className="font-mono text-zinc-300">
                {status?.base_url || 'https://n8n.kavlien.store'}
              </span>
              {status?.latency_ms ? ` (${status.latency_ms} ms)` : ''} &bull; Auth Mode:{' '}
              <span className="font-medium text-zinc-300">{status?.auth_mode || 'session'}</span>
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <a
            href={status?.base_url || 'https://n8n.kavlien.store'}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border border-zinc-700 text-zinc-300 hover:bg-zinc-800 transition-colors"
          >
            <ExternalLink size={12} />
            Open n8n
          </a>
          <button
            onClick={loadData}
            disabled={loading}
            className="p-1.5 rounded-lg border border-zinc-700 text-zinc-400 hover:text-zinc-200 transition-colors"
            title="Refresh status"
          >
            <RefreshCw size={14} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      {/* Security Governance Notice */}
      <div
        className="p-3.5 rounded-xl border flex items-start gap-2.5 text-xs text-amber-300 bg-amber-950/20 border-amber-800/40"
      >
        <ShieldAlert size={16} className="text-amber-400 shrink-0 mt-0.5" />
        <div>
          <span className="font-bold">Explicit Approval Policy: </span>
          All workflow executions and mutating operations are gated by the ApprovalStore. Jarvis and Pulse will queue pending approval requests before running mutating workflows.
        </div>
      </div>

      {/* Discovered Workflows List */}
      <div
        className="p-4 rounded-xl border"
        style={{
          background: 'var(--color-bg)',
          borderColor: 'var(--color-border)',
        }}
      >
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Workflow size={16} style={{ color: 'var(--color-accent)' }} />
            <h4 className="text-xs font-bold uppercase tracking-wider" style={{ color: 'var(--color-text)' }}>
              Discovered Workflows ({workflows.length})
            </h4>
          </div>
        </div>

        {workflows.length === 0 ? (
          <div className="py-8 text-center text-xs text-zinc-500">
            {loading ? 'Discovering workflows from n8n instance...' : 'No workflows discovered or n8n unreachable.'}
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {workflows.map((wf) => (
              <div
                key={wf.id}
                className="p-3.5 rounded-lg border border-zinc-800 bg-zinc-900/60 hover:border-zinc-700 transition-all flex flex-col justify-between"
              >
                <div>
                  <div className="flex items-start justify-between gap-2 mb-1.5">
                    <span className="font-semibold text-xs text-zinc-200 line-clamp-1">
                      {wf.name}
                    </span>
                    <span
                      className={`px-1.5 py-0.5 rounded text-[10px] font-semibold shrink-0 ${
                        wf.active
                          ? 'bg-emerald-950 text-emerald-400'
                          : 'bg-zinc-800 text-zinc-400'
                      }`}
                    >
                      {wf.active ? 'Active' : 'Inactive'}
                    </span>
                  </div>

                  <div className="font-mono text-[11px] text-zinc-500 mb-2">ID: {wf.id}</div>

                  {wf.tags && wf.tags.length > 0 && (
                    <div className="flex flex-wrap gap-1 mb-2">
                      {wf.tags.map((t, idx) => (
                        <span
                          key={idx}
                          className="flex items-center gap-1 px-1.5 py-0.5 rounded bg-zinc-800 text-[10px] text-zinc-400"
                        >
                          <Tag size={9} />
                          {t}
                        </span>
                      ))}
                    </div>
                  )}
                </div>

                <div className="pt-2 border-t border-zinc-800/80 flex items-center justify-between mt-2">
                  <span className="text-[10px] text-zinc-500">
                    Updated: {wf.updatedAt ? new Date(wf.updatedAt).toLocaleDateString() : 'Unknown'}
                  </span>
                  <button
                    onClick={() => {
                      setExecutingId(wf.id);
                      setExecutionResult(null);
                    }}
                    className="flex items-center gap-1 px-2.5 py-1 text-xs font-medium rounded bg-blue-600/90 text-white hover:bg-blue-600 transition-colors"
                  >
                    <Play size={10} />
                    Run Workflow
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Execution Modal */}
      {executingId && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50">
          <div
            className="w-full max-w-lg p-5 rounded-2xl border shadow-2xl space-y-4"
            style={{
              background: 'var(--color-bg)',
              borderColor: 'var(--color-border)',
            }}
          >
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Play size={18} style={{ color: 'var(--color-accent)' }} />
                <h3 className="text-sm font-bold" style={{ color: 'var(--color-text)' }}>
                  Trigger Workflow ({executingId})
                </h3>
              </div>
              <button
                onClick={() => setExecutingId(null)}
                className="text-zinc-400 hover:text-zinc-200"
              >
                &times;
              </button>
            </div>

            <p className="text-xs text-zinc-400">
              Deliver a JSON payload to the workflow. Gated execution will request human approval before running.
            </p>

            <div>
              <label className="block text-xs font-semibold text-zinc-300 mb-1">
                Payload JSON
              </label>
              <textarea
                value={payloadText}
                onChange={(e) => setPayloadText(e.target.value)}
                rows={4}
                className="w-full p-2.5 text-xs font-mono rounded-lg border bg-zinc-900 border-zinc-700 text-zinc-200 outline-none"
                placeholder='{"key": "value"}'
              />
            </div>

            {executionResult && (
              <div
                className={`p-3 rounded-lg text-xs font-mono leading-relaxed ${
                  executionResult.status === 'waiting_approval'
                    ? 'bg-amber-950/40 border border-amber-800 text-amber-300'
                    : executionResult.error
                    ? 'bg-red-950/40 border border-red-800 text-red-300'
                    : 'bg-emerald-950/40 border border-emerald-800 text-emerald-300'
                }`}
              >
                {JSON.stringify(executionResult, null, 2)}
              </div>
            )}

            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setExecutingId(null)}
                className="px-3 py-1.5 text-xs rounded-lg border border-zinc-700 text-zinc-400 hover:text-zinc-200"
              >
                Close
              </button>
              <button
                onClick={() => handleExecuteWorkflow(executingId)}
                disabled={isSubmitting}
                className="flex items-center gap-1.5 px-4 py-1.5 text-xs font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 transition-colors"
              >
                <Send size={12} />
                {isSubmitting ? 'Submitting...' : 'Execute'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
