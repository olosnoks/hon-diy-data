/**
 * Alarms: limits, rate-of-change and combined conditions on any channel
 * (port of hondalog/alarms).
 *
 *     const engine = new AlarmEngine([new AlarmRule({ name: 'ect_high', channel: 'ect', high: 105,
 *                                                     hold_s: 2, severity: 'critical' })], ['ect']);
 *     for (const event of engine.update({ ect: 106 }, t)) ...   // kind: raised | cleared | acknowledged
 *
 * No UI and no I/O. The UI reads active() / top() / severityFor(channel); the
 * recorder and the trigger engine take the events returned by update (or
 * subscribe a callback) and act on event.actions.
 */

export {
  ACTIONS, CRITICAL, WARN, AlarmEngine, AlarmError, AlarmEvent, AlarmRule, AlarmStatus, fillTemplate,
} from './engine';
export type { AlarmAction, AlarmEventKind, AlarmListener, AlarmRuleInit, AlarmState, Severity } from './engine';
