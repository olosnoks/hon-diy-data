/** Trouble code descriptions for a 1996-2000 Honda Civic (D16Y8); port of
 * hondalog/diag/dtc.py. ``HONDA`` holds the manufacturer codes (P1xxx),
 * ``GENERIC`` the SAE codes (P0xxx) this engine can set. The notes after the
 * dash are hints for this car, not service-manual text. Always confirm against
 * the factory manual before replacing parts. */

export const GENERIC: Readonly<Record<string, string>> = {
  P0106: "MAP sensor circuit range/performance",
  P0107: "MAP sensor circuit low voltage",
  P0108: "MAP sensor circuit high voltage",
  P0111: "Intake air temperature sensor range/performance",
  P0112: "Intake air temperature sensor circuit low voltage",
  P0113: "Intake air temperature sensor circuit high voltage",
  P0116: "Engine coolant temperature sensor range/performance",
  P0117: "Engine coolant temperature sensor circuit low voltage",
  P0118: "Engine coolant temperature sensor circuit high voltage",
  P0122: "Throttle position sensor circuit low voltage",
  P0123: "Throttle position sensor circuit high voltage",
  P0125: "Coolant temperature too low for closed-loop fuel control",
  P0128: "Thermostat: coolant stays below regulating temperature",
  P0131: "Primary O2 sensor (sensor 1) circuit low voltage",
  P0132: "Primary O2 sensor (sensor 1) circuit high voltage",
  P0133: "Primary O2 sensor (sensor 1) slow response",
  P0134: "Primary O2 sensor (sensor 1) no activity",
  P0135: "Primary O2 sensor (sensor 1) heater circuit",
  P0137: "Secondary O2 sensor (sensor 2) circuit low voltage",
  P0138: "Secondary O2 sensor (sensor 2) circuit high voltage",
  P0139: "Secondary O2 sensor (sensor 2) slow response",
  P0140: "Secondary O2 sensor (sensor 2) no activity",
  P0141: "Secondary O2 sensor (sensor 2) heater circuit",
  P0171: "Fuel system too lean",
  P0172: "Fuel system too rich",
  P0300: "Random or multiple cylinder misfire",
  P0301: "Cylinder 1 misfire",
  P0302: "Cylinder 2 misfire",
  P0303: "Cylinder 3 misfire",
  P0304: "Cylinder 4 misfire",
  P0325: "Knock sensor circuit",
  P0335: "Crankshaft position (CKP) sensor circuit, no signal",
  P0336: "Crankshaft position (CKP) sensor intermittent or out of range",
  P0340: "Camshaft position sensor circuit",
  P0401: "EGR flow insufficient",
  P0420: "Catalyst efficiency below threshold",
  P0441: "EVAP purge flow incorrect",
  P0443: "EVAP purge control solenoid circuit",
  P0451: "Fuel tank pressure sensor range/performance",
  P0452: "Fuel tank pressure sensor circuit low voltage",
  P0453: "Fuel tank pressure sensor circuit high voltage",
  P0500: "Vehicle speed sensor circuit",
  P0501: "Vehicle speed sensor range/performance",
  P0505: "Idle control system malfunction - idle speed not where the ECU commands it",
  P0560: "System voltage",
  P0562: "System voltage low",
  P0563: "System voltage high",
  P0600: "Serial communication link",
  P0601: "ECU internal memory checksum error",
  P0700: "Automatic transmission control system (A/T only)",
};

export const HONDA: Readonly<Record<string, string>> = {
  P1106: "Barometric pressure circuit range/performance - the BARO sensor is inside the ECU",
  P1107: "Barometric pressure circuit low input - the BARO sensor is inside the ECU",
  P1108: "Barometric pressure circuit high input - the BARO sensor is inside the ECU",
  P1121: "Throttle position lower than expected",
  P1122: "Throttle position higher than expected",
  P1128: "MAP lower than expected",
  P1129: "MAP higher than expected",
  P1149: "Primary O2 sensor (sensor 1) circuit range/performance",
  P1162: "Primary O2 sensor (sensor 1) circuit malfunction",
  P1163: "Primary O2 sensor (sensor 1) slow response",
  P1164: "Primary O2 sensor (sensor 1) circuit range/performance",
  P1165: "Primary O2 sensor (sensor 1) circuit range/performance",
  P1166: "Primary O2 sensor (sensor 1) heater system electrical",
  P1167: "Primary O2 sensor (sensor 1) heater system",
  P1253: "VTEC system malfunction",
  P1257: "VTEC system malfunction",
  P1258: "VTEC system malfunction",
  P1259: "VTEC system malfunction - oil pressure switch or solenoid; check the oil level first",
  P1297: "Electrical load detector (ELD) circuit low input",
  P1298: "Electrical load detector (ELD) circuit high input",
  P1300: "Multiple cylinder misfire detected",
  P1336: "Crankshaft speed fluctuation (CKF) sensor intermittent interruption",
  P1337: "Crankshaft speed fluctuation (CKF) sensor no signal",
  P1359: "CKP/TDC sensor connector disconnection - sensors are in the distributor",
  P1361: "TDC sensor intermittent interruption - sensor is in the distributor",
  P1362: "TDC sensor no signal - sensor is in the distributor",
  P1366: "TDC sensor 2 intermittent interruption",
  P1367: "TDC sensor 2 no signal",
  P1381: "Cylinder position (CYP) sensor intermittent interruption - sensor is in the distributor",
  P1382: "Cylinder position (CYP) sensor no signal - sensor is in the distributor",
  P1456: "EVAP system leak detected (fuel tank side) - check the fuel cap first",
  P1457: "EVAP system leak detected (canister side)",
  P1459: "EVAP purge flow switch malfunction",
  P1486: "Thermostat range/performance",
  P1491: "EGR valve lift insufficient",
  P1498: "EGR valve lift sensor high voltage",
  P1508: "Idle air control (IAC) valve circuit failure",
  P1509: "Idle air control (IAC) valve circuit failure",
  P1519: "Idle air control (IAC) valve circuit failure",
  P1607: "ECU internal circuit failure",
  P1655: "A/T signal line failure (A/T only)",
  P1705: "Automatic transaxle fault (A/T only)",
  P1706: "Automatic transaxle fault (A/T only)",
};

/** Codes worth a second look when chasing an intermittent stall on this engine. */
export const STALL_RELATED: ReadonlySet<string> = new Set([
  "P0107", "P0108", "P0171", "P0172", "P0300", "P0335", "P0336", "P0505",
  "P0562", "P1297", "P1298", "P1300", "P1336", "P1337", "P1359", "P1361",
  "P1362", "P1381", "P1382", "P1508", "P1509", "P1519", "P1607",
]);

const SYSTEMS: Record<string, string> = { P: 'powertrain', C: 'chassis', B: 'body', U: 'network' };

/** Plain text for a code such as 'P0135'. Never throws. */
export function describe(code: string): string {
  const c = code.trim().toUpperCase();
  const text = HONDA[c] ?? GENERIC[c];
  if (text) return text;
  if (c.length === 5 && c[0]! in SYSTEMS) {
    const owner = '13'.includes(c[1]!) ? 'Honda-specific' : 'generic';
    return `(${owner} ${SYSTEMS[c[0]!]} code, no description in hondalog's table)`;
  }
  return '(not a valid OBD2 code)';
}

export function isStallRelated(code: string): boolean {
  return STALL_RELATED.has(code.trim().toUpperCase());
}
