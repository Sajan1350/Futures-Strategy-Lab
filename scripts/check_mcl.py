import pandas as pd
df = pd.read_parquet("data/parquet/MCL.parquet")
print(f"Bars: {len(df):,}")
print(f"Range: {df.index[0]}  to  {df.index[-1]}")
print(f"Price: {df['close'].min():.2f} to {df['close'].max():.2f}")
