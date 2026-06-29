import React, { useState, useEffect, useRef } from 'react';

const App = () => {
  const [data, setData] = useState({
    status: 'OFFLINE',
    balance: '0.00 USDT',
    live_pnl: '+0.00 USDT',
    daily_pnl: '+0.00 USDT',
    win_rate: '0.0%',
    profit_factor: '0.00',
    active_positions: [],
    closed_trades: [],
    logs: ''
  });

  const logsEndRef = useRef(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await fetch('/api/dashboard');
        if (response.ok) {
          const json = await response.json();
          setData(json);
        }
      } catch (error) {
        console.error('Failed to fetch dashboard data', error);
      }
    };

    fetchData(); // initial fetch
    const intervalId = setInterval(fetchData, 3000);

    return () => clearInterval(intervalId);
  }, []);

  useEffect(() => {
    if (logsEndRef.current) {
      logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
    }
  }, [data.logs]);

  const handleAction = async (endpoint) => {
    try {
      await fetch(endpoint, { method: 'POST' });
    } catch (error) {
      console.error(`Failed to execute ${endpoint}`, error);
    }
  };

  const getPnlClass = (pnlStr) => {
    if (!pnlStr) return '';
    if (pnlStr.startsWith('+') || parseFloat(pnlStr) > 0) return 'neon-text-green';
    if (pnlStr.startsWith('-') || parseFloat(pnlStr) < 0) return 'neon-text-red';
    return 'text-slate-300';
  };

  const isBotRunning = data.status.includes('RUNNING');

  return (
    <div className="min-h-screen bg-slate-950 p-6 relative overflow-hidden font-sans">
      {/* Background Effects */}
      <div className="fixed top-[-10%] left-[-10%] w-[40%] h-[40%] rounded-full bg-blue-600/10 blur-[120px] pointer-events-none"></div>
      <div className="fixed bottom-[-10%] right-[-10%] w-[40%] h-[40%] rounded-full bg-emerald-600/10 blur-[120px] pointer-events-none"></div>

      <div className="max-w-7xl mx-auto relative z-10 space-y-6">
        
        {/* Header section */}
        <header className="glass-panel p-6 flex flex-col md:flex-row justify-between items-center gap-4">
          <div className="flex items-center gap-4">
            <div className={`w-3 h-3 rounded-full ${isBotRunning ? 'bg-emerald-400 shadow-[0_0_10px_#34d399] animate-pulse' : 'bg-rose-500 shadow-[0_0_10px_#f43f5e]'}`}></div>
            <h1 className="text-3xl font-black tracking-wider text-slate-100 uppercase">
              OKX
            </h1>
          </div>
          
          <div className="flex items-center gap-3">
            <button 
              onClick={() => handleAction('/api/start')}
              className="px-6 py-2 rounded-lg bg-emerald-500/10 hover:bg-emerald-500/20 border border-emerald-500/50 text-emerald-400 transition-all font-semibold"
            >
              Iniciar Bot
            </button>
            <button 
              onClick={() => handleAction('/api/stop')}
              className="px-6 py-2 rounded-lg bg-rose-500/10 hover:bg-rose-500/20 border border-rose-500/50 text-rose-400 transition-all font-semibold"
            >
              Detener Bot
            </button>
            <button 
              onClick={() => handleAction('/api/reset')}
              className="px-6 py-2 rounded-lg bg-slate-700/30 hover:bg-slate-700/50 border border-slate-600 text-slate-300 transition-all font-semibold"
            >
              Reiniciar Estadísticas
            </button>
          </div>
        </header>

        {/* Top Stats Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-5 gap-4">
          <div className="glass-card">
            <p className="text-xs text-slate-400 uppercase tracking-wider font-semibold mb-1">Estado</p>
            <p className={`text-lg font-bold ${isBotRunning ? 'text-emerald-400' : 'text-slate-300'}`}>{data.status}</p>
          </div>
          <div className="glass-card">
            <p className="text-xs text-slate-400 uppercase tracking-wider font-semibold mb-1">Balance</p>
            <p className="text-xl font-bold text-slate-100">{data.balance}</p>
          </div>
          <div className="glass-card">
            <p className="text-xs text-slate-400 uppercase tracking-wider font-semibold mb-1">PNL en Vivo</p>
            <p className={`text-xl font-bold ${getPnlClass(data.live_pnl)}`}>{data.live_pnl}</p>
          </div>
          <div className="glass-card">
            <p className="text-xs text-slate-400 uppercase tracking-wider font-semibold mb-1">PNL Diario</p>
            <p className={`text-xl font-bold ${getPnlClass(data.daily_pnl)}`}>{data.daily_pnl}</p>
          </div>
          <div className="glass-card">
            <p className="text-xs text-slate-400 uppercase tracking-wider font-semibold mb-1">Tasa de Acierto</p>
            <p className="text-xl font-bold text-blue-400">{data.win_rate} / {data.profit_factor} PF</p>
          </div>
        </div>

        {/* Main Content Grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          
          {/* Left Column: Tables */}
          <div className="lg:col-span-2 space-y-6">
            
            {/* Active Positions */}
            <div className="glass-panel p-6">
              <h2 className="text-lg font-bold text-slate-100 mb-4 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-blue-500"></span>
                Posiciones Activas
              </h2>
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-slate-700/50 text-slate-400 text-sm">
                      <th className="py-3 px-4 font-semibold">Moneda</th>
                      <th className="py-3 px-4 font-semibold">Estrategia</th>
                      <th className="py-3 px-4 font-semibold">Dirección</th>
                      <th className="py-3 px-4 font-semibold">Tamaño</th>
                      <th className="py-3 px-4 font-semibold">Objetivos</th>
                      <th className="py-3 px-4 font-semibold text-right">PNL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.active_positions && data.active_positions.length > 0 ? (
                      data.active_positions.map((pos, idx) => (
                        <tr key={idx} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                          <td className="py-3 px-4 font-medium text-slate-200">{pos[0]}</td>
                          <td className="py-3 px-4 text-slate-400 text-sm">{pos[1]}</td>
                          <td className="py-3 px-4">
                            <span className={`px-2 py-1 rounded text-xs font-bold ${pos[2] === 'LONG' ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'}`}>
                              {pos[2]}
                            </span>
                          </td>
                          <td className="py-3 px-4 text-slate-300 font-mono text-sm">{pos[3]}</td>
                          <td className="py-3 px-4 text-slate-400 text-xs">{pos[4]}</td>
                          <td className={`py-3 px-4 text-right font-mono font-bold ${getPnlClass(pos[5])}`}>{pos[5]}</td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan="6" className="py-8 text-center text-slate-500">Sin posiciones activas</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

            {/* Closed Trades */}
            <div className="glass-panel p-6">
              <h2 className="text-lg font-bold text-slate-100 mb-4 flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-slate-500"></span>
                Últimos Trades
              </h2>
              <div className="overflow-x-auto">
                <table className="w-full text-left border-collapse">
                  <thead>
                    <tr className="border-b border-slate-700/50 text-slate-400 text-sm">
                      <th className="py-3 px-4 font-semibold">Moneda</th>
                      <th className="py-3 px-4 font-semibold">Estrategia</th>
                      <th className="py-3 px-4 font-semibold">Dirección</th>
                      <th className="py-3 px-4 font-semibold">Entrada</th>
                      <th className="py-3 px-4 font-semibold">Salida</th>
                      <th className="py-3 px-4 font-semibold text-right">PNL</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.closed_trades && data.closed_trades.length > 0 && data.closed_trades[0][0] !== '-' ? (
                      data.closed_trades.map((trade, idx) => (
                        <tr key={idx} className="border-b border-slate-800/50 hover:bg-slate-800/30 transition-colors">
                          <td className="py-3 px-4 font-medium text-slate-300">{trade[0]}</td>
                          <td className="py-3 px-4 text-slate-400 text-sm">{trade[1]}</td>
                          <td className="py-3 px-4">
                            <span className={`px-2 py-1 rounded text-xs font-bold ${trade[2] === 'LONG' ? 'bg-emerald-500/10 text-emerald-400' : trade[2] === 'SHORT' ? 'bg-rose-500/10 text-rose-400' : 'bg-slate-700 text-slate-300'}`}>
                              {trade[2]}
                            </span>
                          </td>
                          <td className="py-3 px-4 text-slate-400 font-mono text-sm">{trade[3]}</td>
                          <td className="py-3 px-4 text-slate-400 font-mono text-sm">{trade[4]}</td>
                          <td className={`py-3 px-4 text-right font-mono font-bold ${getPnlClass(trade[6])}`}>{trade[6]}</td>
                        </tr>
                      ))
                    ) : (
                      <tr>
                        <td colSpan="6" className="py-8 text-center text-slate-500">Sin trades recientes</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </div>

          </div>

          {/* Right Column: Logs */}
          <div className="glass-panel p-6 flex flex-col h-[600px] lg:h-auto">
            <h2 className="text-lg font-bold text-slate-100 mb-4 flex items-center gap-2">
              <svg className="w-5 h-5 text-slate-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M8 9l3 3-3 3m5 0h3M4 17h16a2 2 0 002-2V5a2 2 0 00-2-2H4a2 2 0 00-2 2v10a2 2 0 002 2z"></path></svg>
              Terminal del Sistema
            </h2>
            <div className="flex-1 bg-slate-950/80 rounded-lg border border-slate-800 p-4 font-mono text-sm overflow-y-auto terminal-scroll shadow-inner">
              {data.logs ? (
                <div className="whitespace-pre-wrap text-slate-300">
                  {data.logs.split('\n').map((line, i) => {
                    let colorClass = 'text-slate-300';
                    if (line.includes('[ERROR]') || line.includes('Failed')) colorClass = 'text-rose-400';
                    if (line.includes('[SUCCESS]') || line.includes('Profit')) colorClass = 'text-emerald-400';
                    if (line.includes('[INFO]')) colorClass = 'text-blue-400';
                    if (line.includes('[QUANTUM]')) colorClass = 'text-fuchsia-400';
                    return <div key={i} className={`${colorClass} mb-1`}>{line}</div>;
                  })}
                </div>
              ) : (
                <div className="text-slate-600 italic">Esperando salida del terminal...</div>
              )}
              <div ref={logsEndRef} />
            </div>
          </div>

        </div>
      </div>
    </div>
  );
};

export default App;
