// Copia Chart.js de node_modules a static/vendor para servirlo desde Django (la CSP no permite CDN).
import { copyFileSync, existsSync, mkdirSync } from "node:fs";

const candidatos = ["node_modules/chart.js/dist/chart.umd.min.js", "node_modules/chart.js/dist/chart.umd.js"];
const origen = candidatos.find((ruta) => existsSync(ruta));
if (!origen) {
  console.error("No encontré Chart.js en node_modules. Ejecuta primero: npm install");
  process.exit(1);
}
mkdirSync("static/vendor", { recursive: true });
copyFileSync(origen, "static/vendor/chart.umd.js");
console.log(`Chart.js copiado: ${origen} → static/vendor/chart.umd.js`);
