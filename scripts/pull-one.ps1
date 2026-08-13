param([string]$im) docker pull "docker.m.daocloud.io/$im"; if($LASTEXITCODE -eq 0){ docker tag "docker.m.daocloud.io/$im" $im }
