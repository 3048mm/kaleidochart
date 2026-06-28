import pandas as pd
import datetime
from typing import List, Dict, Any, Optional


class ScenarioReporter:
    """
    Generates summaries and exports reports for Scenario Tests.
    Calculates overall metrics like win rate, net profit, benchmark returns,
    max drawdown (with date and tickers), and profit factor.
    """
    
    def generate_summary(
        self, 
        trade_history: List[Dict[str, Any]], 
        initial_capital: float, 
        final_capital: float,
        start_date,
        end_date,
        spy_prices: pd.DataFrame,
        equity_curve: Optional[List[Dict[str, Any]]] = None,
        run_params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Calculates performance metrics based on trade history and capital changes.
        """
        total_trades = len(trade_history)
        winning_trades = sum(1 for t in trade_history if t.get('pnl_amount', 0) > 0)
        losing_trades = sum(1 for t in trade_history if t.get('pnl_amount', 0) <= 0)
        
        win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
        net_profit = final_capital - initial_capital
        total_return_pct = (net_profit / initial_capital) * 100 if initial_capital > 0 else 0.0
        
        # --- Average Holding Period ---
        avg_holding_win = 0.0
        avg_holding_loss = 0.0
        if trade_history:
            df_trades = pd.DataFrame(trade_history)
            if 'entry_date' in df_trades.columns and 'exit_date' in df_trades.columns:
                df_trades['entry_date'] = pd.to_datetime(df_trades['entry_date'])
                df_trades['exit_date'] = pd.to_datetime(df_trades['exit_date'])
                df_trades['holding_days'] = (df_trades['exit_date'] - df_trades['entry_date']).dt.days
                
                win_df = df_trades[df_trades['pnl_amount'] > 0]
                loss_df = df_trades[df_trades['pnl_amount'] <= 0]
                
                if not win_df.empty:
                    avg_holding_win = win_df['holding_days'].mean()
                if not loss_df.empty:
                    avg_holding_loss = loss_df['holding_days'].mean()

        # --- S4: Profit Factor ---
        profit_factor = 0.0
        if trade_history:
            total_gains = sum(t['pnl_amount'] for t in trade_history if t.get('pnl_amount', 0) > 0)
            total_losses = abs(sum(t['pnl_amount'] for t in trade_history if t.get('pnl_amount', 0) < 0))
            if total_losses > 0:
                profit_factor = round(total_gains / total_losses, 2)

        # --- S1: Max Drawdown (with date and tickers) ---
        max_drawdown = self._calculate_max_drawdown(equity_curve)

        # --- Yearly Performance ---
        yearly_stats = {}
        if trade_history:
            df_trades = pd.DataFrame(trade_history)
            df_trades['exit_year'] = pd.to_datetime(df_trades['exit_date']).dt.year
            
            # Pre-calculate SPY yearly returns
            spy_yearly_returns = {}
            if not spy_prices.empty:
                spy_copy = spy_prices.copy()
                spy_copy['date'] = pd.to_datetime(spy_copy['date'])
                spy_copy['year'] = spy_copy['date'].dt.year
                for year, group in spy_copy.groupby('year'):
                    group = group.sort_values('date')
                    start_val = group.iloc[0]['close']
                    end_val = group.iloc[-1]['close']
                    spy_yearly_returns[int(year)] = round(((end_val - start_val) / start_val) * 100, 2)

            for year, group in df_trades.groupby('exit_year'):
                win_count = sum(1 for p in group['pnl_amount'] if p > 0)
                loss_count = len(group) - win_count
                year_int = int(year)
                
                # Per-year profit factor
                yr_gains = sum(p for p in group['pnl_amount'] if p > 0)
                yr_losses = abs(sum(p for p in group['pnl_amount'] if p < 0))
                yr_pf = round(yr_gains / yr_losses, 2) if yr_losses > 0 else 0.0
                
                yearly_stats[year_int] = {
                    'total_trades': len(group),
                    'win_rate': round(win_count / len(group), 4) if len(group) > 0 else 0.0,
                    'net_pnl': round(float(group['pnl_amount'].sum()), 2),
                    'avg_pnl_pct': round(float(group['pnl_pct'].mean() * 100), 2) if 'pnl_pct' in group.columns else 0.0,
                    'profit_factor': yr_pf,
                    'spy_return_pct': spy_yearly_returns.get(year_int, 0.0),
                }

        # --- SPY benchmark return ---
        spy_benchmark_return_pct = 0.0
        if not spy_prices.empty:
            spy_copy = spy_prices.copy()
            spy_copy['date'] = pd.to_datetime(spy_copy['date'])
            sd = pd.to_datetime(start_date)
            ed = pd.to_datetime(end_date)
            period_spy = spy_copy[(spy_copy['date'] >= sd) & (spy_copy['date'] <= ed)].sort_values('date')
            if not period_spy.empty:
                spy_start_val = period_spy.iloc[0]['close']
                spy_end_val = period_spy.iloc[-1]['close']
                if spy_start_val > 0:
                    spy_benchmark_return_pct = round(((spy_end_val - spy_start_val) / spy_start_val) * 100, 2)
        
        # --- Format dates ---
        if hasattr(start_date, 'strftime'):
            start_date_str = start_date.strftime('%Y-%m-%d')
        else:
            start_date_str = str(start_date)
        if hasattr(end_date, 'strftime'):
            end_date_str = end_date.strftime('%Y-%m-%d')
        else:
            end_date_str = str(end_date)
        
        # --- Exit Reasons Breakdown ---
        exit_reasons_stats = {}
        if trade_history:
            df_trades = pd.DataFrame(trade_history)
            if 'exit_reason' in df_trades.columns:
                df_trades['entry_date'] = pd.to_datetime(df_trades['entry_date'])
                df_trades['exit_date'] = pd.to_datetime(df_trades['exit_date'])
                df_trades['holding_days'] = (df_trades['exit_date'] - df_trades['entry_date']).dt.days
                
                for reason, group in df_trades.groupby('exit_reason'):
                    count = len(group)
                    avg_pnl = float(group['pnl_pct'].mean() * 100) if 'pnl_pct' in group.columns else 0.0
                    avg_hold = float(group['holding_days'].mean()) if 'holding_days' in group.columns else 0.0
                    
                    exit_reasons_stats[str(reason)] = {
                        'count': count,
                        'avg_pnl_pct': round(avg_pnl, 2),
                        'avg_holding_days': round(avg_hold, 1)
                    }

        # --- CAGR (Compound Annual Growth Rate) ---
        cagr = 0.0
        if initial_capital > 0 and final_capital > 0:
            try:
                s_dt = pd.to_datetime(start_date).date()
                e_dt = pd.to_datetime(end_date).date()
                days = (e_dt - s_dt).days
                years = days / 365.25
                if years > 0:
                    cagr = ((final_capital / initial_capital) ** (1.0 / years) - 1.0) * 100.0
            except Exception:
                pass

        summary = {
            'start_date': start_date_str,
            'end_date': end_date_str,
            'initial_capital': initial_capital,
            'final_capital': round(final_capital, 2),
            'net_profit': round(net_profit, 2),
            'total_return_pct': round(total_return_pct, 2),
            'cagr': round(cagr, 2),
            'spy_benchmark_return_pct': spy_benchmark_return_pct,
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'win_rate': round(win_rate, 4),
            'profit_factor': profit_factor,
            'avg_holding_days_win': round(avg_holding_win, 2),
            'avg_holding_days_loss': round(avg_holding_loss, 2),
            'max_drawdown': max_drawdown,
            'yearly_performance': yearly_stats,
            'exit_reasons': exit_reasons_stats,
        }
        
        # Extract top 5 most profitable trades by PnL amount
        top_profitable = []
        if trade_history:
            df_trades = pd.DataFrame(trade_history)
            if 'pnl_amount' in df_trades.columns:
                df_sorted = df_trades.sort_values('pnl_amount', ascending=False).head(5)
                for _, row in df_sorted.iterrows():
                    # Safely handle Timestamp formatting
                    e_date = row.get('entry_date')
                    x_date = row.get('exit_date')
                    e_str = e_date.strftime('%Y-%m-%d') if hasattr(e_date, 'strftime') else str(e_date)
                    x_str = x_date.strftime('%Y-%m-%d') if hasattr(x_date, 'strftime') else str(x_date)
                    
                    top_profitable.append({
                        'ticker': row.get('ticker'),
                        'entry_date': e_str,
                        'exit_date': x_str,
                        'entry_price': round(float(row.get('entry_price')), 2) if 'entry_price' in row else 0.0,
                        'exit_price': round(float(row.get('exit_price')), 2) if 'exit_price' in row else 0.0,
                        'pnl_amount': round(float(row.get('pnl_amount')), 2),
                        'pnl_pct': round(float(row.get('pnl_pct') * 100), 2) if 'pnl_pct' in df_trades.columns else 0.0,
                        'shares': int(row.get('shares', 0)),
                        'exit_reason': row.get('exit_reason')
                    })
        summary['top_profitable_trades'] = top_profitable
        
        # S3: Include run parameters if provided
        if run_params:
            summary['run_params'] = run_params
        
        return summary

    @staticmethod
    def _calculate_max_drawdown(equity_curve: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
        """
        Calculates Max Drawdown from equity curve with date and tickers at the deepest point.
        
        Returns:
            Dict with 'pct', 'amount', 'peak_date', 'trough_date', 'trough_tickers'.
        """
        if not equity_curve or len(equity_curve) < 2:
            return {'pct': 0.0, 'amount': 0.0, 'peak_date': None, 'trough_date': None, 'trough_tickers': []}
        
        peak = equity_curve[0]['total_equity']
        peak_date = equity_curve[0]['date']
        max_dd_pct = 0.0
        max_dd_amount = 0.0
        max_dd_peak_date = peak_date
        max_dd_trough_date = peak_date
        max_dd_trough_tickers = []

        for snap in equity_curve:
            equity = snap['total_equity']
            if equity > peak:
                peak = equity
                peak_date = snap['date']
            
            dd_amount = peak - equity
            dd_pct = dd_amount / peak if peak > 0 else 0.0
            
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
                max_dd_amount = dd_amount
                max_dd_peak_date = peak_date
                max_dd_trough_date = snap['date']
                max_dd_trough_tickers = snap.get('held_tickers', '').split(',') if snap.get('held_tickers') else []

        # Format dates for JSON serialization
        def fmt_date(d):
            if hasattr(d, 'strftime'):
                return d.strftime('%Y-%m-%d')
            return str(d) if d else None

        return {
            'pct': round(max_dd_pct * 100, 2),
            'amount': round(max_dd_amount, 2),
            'peak_date': fmt_date(max_dd_peak_date),
            'trough_date': fmt_date(max_dd_trough_date),
            'trough_tickers': max_dd_trough_tickers,
        }

    def export_trade_logs(self, trade_history: List[Dict[str, Any]], filepath: str):
        """
        Exports the detailed trade history to a CSV file.
        """
        if not trade_history:
            # Export empty CSV with headers if no trades
            pd.DataFrame(columns=[
                'ticker', 'entry_date', 'exit_date', 'entry_price', 'exit_price',
                'shares', 'amount', 'exit_reason', 'pnl_pct', 'pnl_amount', 'capital_after'
            ]).to_csv(filepath, index=False)
            return
            
        df = pd.DataFrame(trade_history)
        
        # Select and order relevant columns
        cols = [
            'ticker', 'entry_date', 'exit_date', 'entry_price', 'exit_price', 
            'shares', 'amount', 'exit_reason', 'pnl_pct', 'pnl_amount', 'capital_after'
        ]
        
        # Filter only existing columns just in case
        existing_cols = [c for c in cols if c in df.columns]
        df = df[existing_cols]
        
        # Format dates if they are Timestamps
        if 'entry_date' in df.columns:
            df['entry_date'] = pd.to_datetime(df['entry_date']).dt.strftime('%Y-%m-%d')
        if 'exit_date' in df.columns:
            df['exit_date'] = pd.to_datetime(df['exit_date']).dt.strftime('%Y-%m-%d')
            
        df.to_csv(filepath, index=False)

    def export_equity_curve(self, equity_curve: List[Dict[str, Any]], filepath: str):
        """
        Exports the daily equity curve to a CSV file (S2).
        """
        if not equity_curve:
            pd.DataFrame(columns=[
                'date', 'cash', 'invested', 'total_equity', 'positions', 'held_tickers'
            ]).to_csv(filepath, index=False)
            return
        
        df = pd.DataFrame(equity_curve)
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
        df.to_csv(filepath, index=False)
