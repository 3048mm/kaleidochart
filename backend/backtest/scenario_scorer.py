import pandas as pd

class ScenarioScorer:
    """
    Evaluates trading signals from multiple strategies and scores them.
    Aggregates signals for a specific date, scores them based on the number of
    strategy hits, and resolves ties using a secondary metric (e.g., rs21_rank).
    """
    def __init__(self, target_group_prefix: str = 'Rise - Pickup'):
        self.target_group_prefix = target_group_prefix

    def score_signals(self, signals_df: pd.DataFrame, date: pd.Timestamp) -> pd.DataFrame:
        """
        Scores signals for a specific date.
        
        Args:
            signals_df: DataFrame containing signals from all strategies.
            date: The target date to evaluate.
            
        Returns:
            A DataFrame of unique symbols, their score, and rs21_rank, sorted by score and rank.
        """
        if signals_df.empty:
            return pd.DataFrame()
            
        # Filter for the target date
        day_signals = signals_df[signals_df['date'] == date]
        if day_signals.empty:
            return pd.DataFrame()
            
        # Filter by strategy group prefix if specified
        if self.target_group_prefix:
            day_signals = day_signals[day_signals['strategy_name'].str.startswith(self.target_group_prefix)]
            
        if day_signals.empty:
            return pd.DataFrame()
            
        # Aggregate by symbol_id to count hits (score) and keep rs21_rank for tie-breaking
        # Assuming rs21_rank is the same for a symbol across different strategies on the same day
        scored_df = day_signals.groupby(['symbol_id', 'ticker']).agg(
            score=('strategy_name', 'count'),
            rs21_rank=('rs21_rank', 'first')
        ).reset_index()
        
        # Sort by score (descending) and then rs21_rank (descending)
        scored_df = scored_df.sort_values(by=['score', 'rs21_rank'], ascending=[False, False])
        
        return scored_df
