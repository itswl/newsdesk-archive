// Worker 自查逻辑的测试。必须在 UTC 下跑——Worker 就跑在 UTC，
// 而这里最要紧的一条正是时区：北京时间的时间戳被当成 UTC 会差整整 8 小时，
// 结果是机器关了 26 小时它只看到 18 小时，监控永远晚一个时区才响。
//
//   TZ=UTC node deploy/worker.test.mjs
import { evaluate } from './worker.js';

if (Intl.DateTimeFormat().resolvedOptions().timeZone !== 'UTC') {
  console.error('!! 必须用 TZ=UTC 跑，否则时区那几条测不出来'); process.exit(2);
}
const NOW = Date.parse('2026-09-15T10:00:00Z');      // 北京 18:00
let pass = true;
const t = (n, c, x = '') => { pass &&= c; console.log(`  ${c ? '✓' : '✗'} ${n}${x ? '  ' + x : ''}`); };
const mk = (o) => JSON.stringify(Object.assign({
  generated: '2026-09-15T18:00:00+08:00', generated_epoch: NOW / 1000,
  date: '2026-09-15', tasks: { ai: { status: 'ok', finished: '06:35' } } }, o));

console.log('── 新鲜时不打扰 ──');
let r = await evaluate(mk({}), NOW, 26);
t('刚生成', !r.bad, `age=${r.age.toFixed(2)}h`);
r = await evaluate(mk({ generated_epoch: NOW / 1000 - 19 * 3600 }), NOW, 26);
// 相邻两档最长间隔 8.75h（21:45 → 次日 06:30），夜里合盖次日中午才补跑约 19h，
// 阈值必须容得下它，否则每周都会误报几次，然后这条告警就被当噪声忽略了
t('19h：夜里合盖 + 次日补跑', !r.bad, `age=${r.age.toFixed(1)}h`);

console.log('── 陈旧时要叫 ──');
r = await evaluate(mk({ generated_epoch: NOW / 1000 - 27 * 3600 }), NOW, 26);
t('27h', r.bad, r.title);
r = await evaluate(mk({ generated_epoch: NOW / 1000 - 72 * 3600 }), NOW, 26);
t('关机三天', r.bad, `age=${r.age.toFixed(0)}h`);

console.log('── 时区：真实产出是北京 09-14 06:00，距 now 恰好 36h ──');
const naive  = await evaluate(JSON.stringify({ generated: '2026-09-14T06:00:00',       date: '2026-09-14', tasks: {} }), NOW, 26);
const offset = await evaluate(JSON.stringify({ generated: '2026-09-14T06:00:00+08:00', date: '2026-09-14', tasks: {} }), NOW, 26);
const epoch  = await evaluate(mk({ generated: '解析不了的东西', generated_epoch: NOW / 1000 - 36 * 3600 }), NOW, 26);
t('不带偏移量 → 算错（所以产出端必须带时区）', Math.abs(naive.age - 36) > 7,
  `age=${naive.age.toFixed(1)}h，差 ${(naive.age - 36).toFixed(0)}h`);
t('带偏移量   → 正确', Math.abs(offset.age - 36) < 0.01);
t('epoch      → 正确，且优先于 ISO', Math.abs(epoch.age - 36) < 0.01);

console.log('── 异常输入一律当作出事 ──');
t('非 JSON（桶里只发布了一半）', (await evaluate('<html>404</html>', NOW, 26)).bad);
t('缺时间戳', (await evaluate('{}', NOW, 26)).bad);
t('时间在未来不告警（时钟偏差不制造假警报）',
  !(await evaluate(mk({ generated_epoch: NOW / 1000 + 3600 }), NOW, 26)).bad);

console.log('\n' + (pass ? '全部通过' : '有失败'));
process.exit(pass ? 0 : 1);
