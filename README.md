# Montagne & Paesi Control Center

Dashboard unica per controllare container, pannelli, log ed email delle automazioni di Montagne & Paesi.

## Installazione con Portainer

1. Aprire **Stacks** e creare uno stack dal repository GitHub.
2. Usare `docker-compose.yml` come Compose path.
3. Eseguire il deploy e aprire `http://IP-QNAP:8091`.
4. Aprire **Impostazioni** e inserire server IMAP, email e password delle due caselle.

Le password vengono cifrate e conservate nel volume Docker `dashboard_data`; non vengono inserite nel repository o mostrate nuovamente nel pannello.

## Sicurezza

La socket Docker è montata in sola lettura. La dashboard non contiene pulsanti per fermare, eliminare o riavviare container. Non pubblicare la porta 8091 direttamente su Internet.

## Servizi inclusi

- Comunicati stampa (`8088`)
- Instagram (`8080`)
- Amazon (`8085`)
- Prezzi carburanti (`8087`)
- Meteo (`8124`)
- Oroscopo (`8086`)
- Foto del giorno (`8090`)
- WAHA (`3000`)
- RSS Bot
