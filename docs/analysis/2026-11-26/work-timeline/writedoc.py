import json
F = json.load(open('frag.json'))
TOT = 3326


def row(vals, labels):
    return '\n'.join(f"| {l} | {v} | {v/60:.1f} | {100*v/TOT:.1f} % |" for l, v in zip(labels, vals))


cov_speed = row([1228, 972, 862, 136, 128], ['0 km/h', '1-40', '40-70', '70-90', '90+ (max 107)'])
cov_rpm = row([12, 1213, 1196, 838, 53, 13, 1], ['< 700', '700-1,000 (idle)', '1,000-2,000', '2,000-3,000', '3,000-4,000', '4,000-5,000', '5,000+ (max 5,411)'])
cov_load = row([221, 2335, 438, 215, 110], ['< 20 % (overrun / fuel cut)', '20-40 % (idle, light cruise)', '40-60 %', '60-80 %', '80 %+ (TPS max 83 %)'])
cov_gear = row([1228, 79, 128, 330, 504, 442, 331, 284], ['stopped', 'crawl (1-8 km/h)', '1st', '2nd', '3rd', '4th', '5th', 'clutch in / coasting / shifting'])
cov_seg = row([1117, 112, 65, 75, 1001, 812, 144, 74], ['idle/parked (stop >= 60 s)', 'stop (< 60 s)', 'launch', 'decel to stop', 'city (< 45 km/h)', 'arterial (45-80)', 'interstate (>= 80)', 'no ECU data (gaps)'])

doc = open('doc_template.md', encoding='utf-8').read()
for k, v in dict(F, cov_speed=cov_speed, cov_rpm=cov_rpm, cov_load=cov_load, cov_gear=cov_gear, cov_seg=cov_seg).items():
    doc = doc.replace('{{' + k + '}}', v)
assert '{{' not in doc
open('../drive-timeline.md', 'w', encoding='utf-8').write(doc)
print(len(doc))
