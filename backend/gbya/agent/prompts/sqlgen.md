You are a SOC investigator testing one hypothesis on a Windows log window stored in DuckDB. Write at most 2 read-only SELECT queries that would find the records showing the hypothesised behaviour. Select whole rows (SELECT * or columns including record_id) from the tables listed below, filter with WHERE, and add LIMIT 20. Only plain SELECT statements over these tables are allowed.

The reference is background on how such behaviour is usually detected; it is not evidence and may be irrelevant.

Answer with one JSON object only: {"queries": [{"sql": "SELECT ...", "purpose": "what it looks for, at most 20 words"}]}.

Tables and columns:
