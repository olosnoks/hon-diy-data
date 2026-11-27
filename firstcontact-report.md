# hondalog first contact

- when: 2026-11-25 17:38:24
- port: `COM21`
- **verdict: ISO only**

The ECU answers standard OBD2 (ISO 9141-2) but not Honda RAM reads on this pin. Codes, freeze frame and readiness work; live data is limited to about 4-8 PIDs per second. Honda reads may need the 3-pin service connector instead of OBD2 pin 7.

## Next steps

1. Use the OBD2 functions (codes, freeze frame, readiness, PIDs); they work now.
2. For fast logging try the Honda protocol on the 3-pin service connector instead of OBD2 pin 7, and run this again.
3. Send this report and the .hex.log along if you ask for help.

## Steps

| # | step | result | detail |
|---|---|---|---|
| 1 | adapter | FAIL | unknown, unknown |
| 2 | open port | OK | USB latency 1 ms |
| 3 | echo test | OK | echo after 7.4 ms |
| 4 | passive listen | OK | line quiet for 2 s |
| 5 | Honda read 0x00 (no wake-up) | FAIL | no valid reply |
| 6 | Honda read 0x00 (after wake-up) | FAIL | no valid reply |
| 7 | alternate framings | FAIL | nothing came back to parse |
| 8 | ISO 9141-2 init | OK | ISO 9141-2 via 5-baud init, key bytes 08 08, sync after 153 ms |
| 9 | OBD2 snapshot | OK | stored codes: none; 16 PIDs supported |

## Adapter

- kind: unknown, driver: -, USB id: ?
- chip: -, manufacturer: -, product: -, serial: -
- verdict: unknown
- latency timer: - ms
- COM21 does not exist: cable not plugged in, or no driver bound

## ISO 9141-2

init: ISO 9141-2 via 5-baud init, key bytes 08 08

```
OBD2 snapshot
  link: ISO 9141-2 via 5-baud init, key bytes 08 08
  status: MIL off, 0 stored DTC(s), not ready: catalyst, evaporative system, O2 sensor
    misfire                    ready
    fuel system                ready
    comprehensive components   ready
    catalyst                   NOT READY
    evaporative system         NOT READY
    O2 sensor                  NOT READY
    O2 sensor heater           ready
  stored codes: none
  pending codes: none
  live values (supported PIDs: 01 03 04 05 06 07 0B 0C 0D 0E 0F 11 13 14 15 1C):
    Fuel system status: closed loop
    Calculated load: 24.3 %
    Coolant temperature: 75 °C
    Short term fuel trim B1: 7.8 %
    Long term fuel trim B1: 0 %
    Intake manifold pressure: 24 kPa
    Engine speed: 787.25 rpm
    Vehicle speed: 0 km/h
    Ignition advance: 16 ° BTDC
    Intake air temperature: 31 °C
    Throttle position: 8.2 %
    O2 sensors present: B1S1 B1S2
    O2 sensor B1S1: 0.23 V
    O2 sensor B1S2: 0.975 V
    OBD standard: OBD-II (CARB)
```

## Byte trace

Times in ms from the first event. TX = sent, RX = received (our own echo included), BREAK = line held low / released, BAUD = speed change.

```
       0.0  NOTE  echo test
       0.0  TX    55
       7.4  RX    55
       7.4  NOTE  passive listen
    2068.7  NOTE  Honda read 0x00, no wake-up bytes
    2068.8  TX    20 05 00 02 D9
    2070.6  RX    20
    2087.7  RX    05 00 02 D9
    2319.1  TX    20 05 00 02 D9
    2328.4  RX    20 05 00 02 D9
    2534.6  NOTE  wake-up bytes, then Honda read 0x00
    2564.8  TX    68 6A F5 AF BF B3 B2 C1 DB B3 E9
    2925.1  RX    68 6A F5 AF BF B3 B2 C1 DB B3 E9
    2927.5  TX    20 05 00 02 D9
    2935.9  RX    20 05 00 02 D9
    3159.4  TX    20 05 00 02 D9
    3175.8  RX    20 05 00 02 D9
    3376.1  NOTE  default read, raw capture
    3394.2  TX    20 05 00 02 D9
    3398.9  RX    20 05 00
    3414.7  RX    02 D9
    3516.4  NOTE  ISO 9141-2 init
    3516.4  BREAK off (line high)
    3516.5  BAUD  10400
    3868.1  NOTE  5-baud init, address 33: 0110011001
    3868.1  BREAK on (line low)
    4068.7  BREAK off (line high)
    4468.4  BREAK on (line low)
    4868.5  BREAK off (line high)
    5268.8  BREAK on (line low)
    5669.5  BREAK off (line high)
    6021.3  RX    55
    6037.6  RX    08
    6060.1  RX    08
    6090.6  TX    F7
    6101.4  RX    F7
    6133.3  RX    CC
    6133.4  NOTE  session up: ISO 9141-2 via 5-baud init, key bytes 08 08
    6191.0  TX    68 6A F1 01 00 C4
    6199.1  RX    68 6A F1 01 00 C4
    6248.9  RX    48 6B 10 41 00 BE 3E B8 10 C8
    6325.6  TX    68 6A F1 01 01 C5
    6343.8  RX    68 6A F1 01 01 C5
    6375.5  RX    48 6B 10 41 01 00
    6390.2  RX    07 65 25 96
    6466.4  TX    68 6A F1 03 C6
    6469.8  RX    68 6A
    6485.8  RX    F1 03 C6
    6734.6  TX    68 6A F1 03 C6
    6743.0  RX    68 6A F1 03 C6
    6996.9  TX    68 6A F1 07 CA
    6998.3  RX    D0
    7016.2  RX    D4 F1 07 CA
    7262.6  TX    68 6A F1 07 CA
    7271.4  RX    68 6A F1 07 CA
    7531.6  TX    68 6A F1 02 02 00 C7
    7544.4  RX    68 6A F1 02 02 00 C7
    7575.0  RX    48 6B 10 42
    7607.8  RX    02 00 00 00 07
    7673.2  TX    68 6A F1 01 03 C7
    7698.2  RX    68 6A F1 01 03 C7
    7719.0  RX    48 6B 10 41
    7734.9  RX    03 02 00 09
    7800.1  TX    68 6A F1 01 04 C8
    7815.8  RX    68 6A F1 01 04 C8
    7846.8  RX    48 6B 10 41
    7862.3  RX    04 3E 46
    7938.8  TX    68 6A F1 01 05 C9
    7945.6  RX    68 6A
    7959.4  RX    F1 01 05 C9
    7992.2  RX    48 6B 10 41 05 73 7C
    8062.5  TX    68 6A F1 01 06 CA
    8071.3  RX    68 6A F1 01 06 CA
    8120.4  RX    48 6B 10 41 06 8A 94
    8189.9  TX    68 6A F1 01 07 CB
    8199.6  RX    68 6A F1 01 07 CB
    8247.7  RX    48 6B 10 41 07 80 8B
    8312.6  TX    68 6A F1 01 0B CF
    8330.7  RX    68 6A F1 01 0B CF
    8359.3  RX    48 6B 10 41
    8374.4  RX    0B 18 27
    8438.8  TX    68 6A F1 01 0C D0
    8455.4  RX    68 6A F1 01 0C D0
    8485.8  RX    48 6B
    8502.3  RX    10 41 0C 0C 4D 69
    8577.0  TX    68 6A F1 01 0D D1
    8582.9  RX    68 6A F1 01 1D
    8597.4  RX    45
    8843.1  TX    68 6A F1 01 0D D1
    8856.4  RX    68 6A F1 01 0D D1
    8886.7  RX    48 6B 10 41 0D
    8901.8  RX    00 11
    8968.2  TX    68 6A F1 01 0E D2
    8983.1  RX    68 6A F1 01 0E D2
    9015.4  RX    48 6B 10 41
    9030.3  RX    0E A0 B2
    9104.5  TX    68 6A F1 01 0F D3
    9110.4  RX    68 6A F1 01
    9125.5  RX    0F F3
    9373.8  TX    68 6A F1 01 0F D3
    9383.2  RX    68 6A F1 01 0F D3
    9431.3  RX    48 6B 10 41 0F 47 5A
    9497.7  TX    68 6A F1 01 11 D5
    9511.0  RX    68 6A F1 01 11 D5
    9543.1  RX    48 6B 10 41 11 15
    9557.4  RX    2A
    9630.2  TX    68 6A F1 01 13 D7
    9639.2  RX    68 6A F1 01 13 D7
    9687.3  RX    48 6B 10 41 13 03 1A
    9763.7  TX    68 6A F1 01 14 D8
    9765.1  RX    68
    9798.0  RX    6A F1 01 14 D8
    9815.6  RX    48 6B 10 41 14 2E 8C D2
    9889.3  TX    68 6A F1 01 15 D9
    9894.0  RX    68 6A F1
    9909.9  RX    01 15 D9
    9943.9  RX    48 6B 10 41 15 C3 8D 69
   10014.7  TX    68 6A F1 01 1C E0
   10022.8  RX    68 6A F1 01 1C E0
   10071.2  RX    48 6B 10 41 1C 01 21
   10140.2  BREAK off (line high)
   10140.2  BAUD  9600
```
