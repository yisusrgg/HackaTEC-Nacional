// Copia Chart.js y las fuentes de node_modules a static/vendor para servirlos desde Django (la CSP no permite CDN).
import { copyFileSync, existsSync, mkdirSync } from "node:fs";

function copiar(candidatos, destino, nombre) {
  const origen = candidatos.find((ruta) => existsSync(ruta));
  if (!origen) {
    console.error(`No encontré ${nombre} en node_modules. Ejecuta primero: npm install`);
    process.exit(1);
  }
  copyFileSync(origen, destino);
  console.log(`${nombre} copiado: ${origen} → ${destino}`);
}

mkdirSync("static/vendor/fuentes", { recursive: true });

copiar(["node_modules/chart.js/dist/chart.umd.min.js", "node_modules/chart.js/dist/chart.umd.js"],
  "static/vendor/chart.umd.js", "Chart.js");
copiar(["node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2"],
  "static/vendor/fuentes/inter.woff2", "Inter");
copiar(["node_modules/@fontsource-variable/inter-tight/files/inter-tight-latin-wght-normal.woff2"],
  "static/vendor/fuentes/inter-tight.woff2", "Inter Tight");
copiar(["node_modules/@fontsource-variable/jetbrains-mono/files/jetbrains-mono-latin-wght-normal.woff2"],
  "static/vendor/fuentes/jetbrains-mono.woff2", "JetBrains Mono");