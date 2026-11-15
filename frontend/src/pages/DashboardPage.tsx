import { useEffect, useState } from 'react';
import { Cpu, ShieldCheck, Mic, HardDrive, Sparkles } from 'lucide-react';
import { EnergyDashboard } from '../components/Dashboard/EnergyDashboard';
import { CostComparison } from '../components/Dashboard/CostComparison';
import { TraceDebugger } from '../components/Dashboard/TraceDebugger';
import { TaskCenter } from '../components/Desktop/TaskCenter';
import { RoutineManager } from '../components/Desktop/RoutineManager';
import { fetchSystemCapabilities, type SystemCapabilities } from '../lib/api';

export function DashboardPage() {
  const [caps, setCaps] = useState<SystemCapabilities | null>(null);
  const now = new Date();
  const stamp = now.toISOString().replace('T', ' ').slice(0, 19) + ' UTC';

  useEffect(() => {
    fetchSystemCapabilities().then(setCaps).catch(() => {});
  }, []);

  return (
    <div className="flex-1 overflow-y-auto px-6 py-10">
      <div className="max-w-5xl mx-auto">
        <header className="mb-6">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <Sparkles size={20} style={{ color: 'var(--color-accent)' }} />
              <h1 className="text-xl font-bold tracking-tight" style={{ color: 'var(--color-text)' }}>
                Jarvis Command Center
              </h1>
            </div>
            <div className="text-xs font-mono" style={{ color: 'var(--color-text-tertiary)' }}>
              {stamp}
            </div>
          </div>
          <p className="text-sm mt-1.5 max-w-2xl" style={{ color: 'var(--color-text-secondary)' }}>
            Autonomous personal AI assistant operations, background execution, voice intelligence, and hardware telemetry.
          </p>
        </header>

        {/* Hardware & Runtime Capability Bar */}
        {caps && (
          <div
            className="grid grid-cols-2 sm:grid-cols-4 gap-3 p-4 rounded-xl mb-6 shadow-xs"
            style={{
              background: 'var(--color-bg-secondary)',
              border: '1px solid var(--color-border)',
            }}
          >
            <div className="flex items-center gap-2.5">
              <Cpu size={18} style={{ color: 'var(--color-accent)' }} />
              <div>
                <div className="text-[10px] uppercase font-semibold" style={{ color: 'var(--color-text-tertiary)' }}>
                  Processor
                </div>
                <div className="text-xs font-medium truncate" style={{ color: 'var(--color-text)' }} title={caps.hardware.cpu_name}>
                  {caps.hardware.cpu_name || 'Ryzen 5 5600H'}
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2.5">
              <HardDrive size={18} style={{ color: '#38bdf8' }} />
              <div>
                <div className="text-[10px] uppercase font-semibold" style={{ color: 'var(--color-text-tertiary)' }}>
                  GPU &amp; VRAM
                </div>
                <div className="text-xs font-medium truncate" style={{ color: 'var(--color-text)' }}>
                  {caps.hardware.gpu_name ? `${caps.hardware.gpu_name} (${caps.hardware.gpu_vram_total_mb}MB)` : 'GTX 1650 (4 GB VRAM)'}
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2.5">
              <Mic size={18} style={{ color: '#a855f7' }} />
              <div>
                <div className="text-[10px] uppercase font-semibold" style={{ color: 'var(--color-text-tertiary)' }}>
                  Voice Pipeline
                </div>
                <div className="text-xs font-medium truncate" style={{ color: 'var(--color-text)' }}>
                  &quot;Hey Jarvis&quot; (Echo Guard)
                </div>
              </div>
            </div>

            <div className="flex items-center gap-2.5">
              <ShieldCheck size={18} style={{ color: '#22c55e' }} />
              <div>
                <div className="text-[10px] uppercase font-semibold" style={{ color: 'var(--color-text-tertiary)' }}>
                  Action Security
                </div>
                <div className="text-xs font-medium truncate" style={{ color: 'var(--color-text)' }}>
                  5 Tiers &bull; Explicit Approval
                </div>
              </div>
            </div>
          </div>
        )}

        {/* Autonomous Background Tasks */}
        <TaskCenter />

        {/* Unified Routines & Scheduling */}
        <RoutineManager />

        {/* Live Telemetry & Cost Comparison */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 mb-6">
          <EnergyDashboard />
          <CostComparison />
        </div>

        {/* Trace Debugger */}
        <TraceDebugger />
      </div>
    </div>
  );
}
