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
- WhatsApp / WAHA (`3000`) con l'ultimo articolo inviato sul canale (log di `rssbot_montagne`)
- Eventi (`8095`, mep-eventi): resoconto dell'ultimo giro. Se il pannello eventi ha una password, impostare `EVENTI_WEB_PASSWORD` nello stack

## Novità 2.0.0

- Nuova grafica chiara, schede con stato Operativo / Da controllare / Problema.
- Instagram: pubblicati oggi, coda, quota Meta e ultimo post con foto e orario (richiede igbot ≥ 2.8.1).
- Carburanti e oroscopo: ultimo caricamento riuscito; l'errore compare solo se oggi l'articolo non è stato caricato.
- Meteo: data e ora dell'ultimo articolo e eventuali errori.
- WhatsApp: ultimo articolo inviato sul canale con foto; la scheda RSS Bot è stata rimossa.
- Eventi: resoconto dell'ultimo giro (nuovi, aggiornati, errori) e prossimo giro.
