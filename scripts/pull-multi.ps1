param([string]$im)
foreach($m in @('docker.1ms.run','hub.rat.dev','dockerproxy.net','docker.m.daocloud.io')){
  docker pull "${m}/${im}"
  if($LASTEXITCODE -eq 0){ docker tag "${m}/${im}" $im; break }
}
