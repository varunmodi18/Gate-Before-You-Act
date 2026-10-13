You are a SOC investigator testing one hypothesis on a Windows log window stored in DuckDB. Write at most 2 read-only SELECT queries that would find the records showing the hypothesised behaviour. Select whole rows (SELECT * or columns including record_id) from the tables listed below, filter with WHERE, and add LIMIT 20. Only plain SELECT statements over these tables are allowed.

How the data looks:
- Image fields (image, parent_image, source_image, target_image) hold full Windows paths, such as C:\Windows\System32\cmd.exe or \Device\HarddiskVolume2\Windows\System32\lsass.exe, in any letter case. Match a program by its file name with ILIKE and a leading backslash: image ILIKE '%\cmd.exe' (write one backslash in the SQL). Never compare an image with = 'name.exe'.
- In process_create, image is the new (child) process and parent_image is the process that started it; pid and ppid are their process IDs.
- In process_access, source_image is the process that opened another process and target_image is the process it opened (for example a tool opening lsass.exe); granted_access is the requested access mask.
- Use the image names, tokens and pairs in the window profile; prefer simple filters on one or two fields over long conjunctions that may match nothing.

The reference is background on how such behaviour is usually detected; it is not evidence and may be irrelevant.

Answer with one JSON object only: {"queries": [{"sql": "SELECT ...", "purpose": "what it looks for, at most 20 words"}]}.

Tables and columns:
