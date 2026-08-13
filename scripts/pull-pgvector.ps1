docker pull hub.rat.dev/pgvector/pgvector:pg16 2>&1
if($LASTEXITCODE -eq 0){ docker tag hub.rat.dev/pgvector/pgvector:pg16 pgvector/pgvector:pg16; "PGVECTOR_DONE" } else { "FAIL rc=$LASTEXITCODE" }
