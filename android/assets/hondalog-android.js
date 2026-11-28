/*
 * hondalog Android app: runs before the web app (AssetServer puts it first in
 * <head>). It gives the page what Chrome would, backed by the app's Java code
 * (window.HondalogAndroid, see Bridge.java):
 *   - navigator.usb: the WebUSB subset src/platform/webusb uses (FTDI, CH340),
 *     with "connect"/"disconnect" events;
 *   - downloads (an <a download> click on a blob:/data: URL) saved to
 *     Download/hondalog instead of being dropped by the WebView.
 * Plain ES5 on purpose: it must run even where the web app itself cannot.
 */
(function () {
  'use strict';
  var B = window.HondalogAndroid;
  // Always take over navigator.usb: a WebView's own WebUSB (if one ever has it)
  // can't reach the device, which the app holds.
  if (!B || window.__hondalogShim) return;
  window.__hondalogShim = true;

  var ERR = '\u0001';
  var pending = {};
  var nextId = 1;

  function domError(name, message) {
    try { return new DOMException(message, name); } catch (e) { var x = new Error(message); x.name = name; return x; }
  }

  /** Call an async Bridge method: first argument is the callback id. */
  function call(method, args) {
    return new Promise(function (resolve, reject) {
      var id = nextId++;
      pending[id] = { resolve: resolve, reject: reject };
      try {
        B[method].apply(B, [id].concat(args || []));
      } catch (e) {
        delete pending[id];
        reject(e);
      }
    });
  }

  window.__hondalogUsbDone = function (id, ok, json) {
    var p = pending[id];
    if (!p) return;
    delete pending[id];
    var v = json == null ? null : JSON.parse(json);
    if (ok) p.resolve(v);
    else p.reject(domError((v && v.name) || 'NetworkError', (v && v.message) || 'USB error'));
  };

  function u8(data) {
    if (data == null) return new Uint8Array(0);
    if (data instanceof Uint8Array) return data;
    if (data instanceof ArrayBuffer) return new Uint8Array(data);
    if (ArrayBuffer.isView(data)) return new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
    throw new TypeError('not a BufferSource');
  }

  function b64(bytes) {
    var s = '';
    for (var i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
    return btoa(s);
  }

  function unb64(str) {
    var s = atob(str || '');
    var out = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) out[i] = s.charCodeAt(i);
    return out;
  }

  function dataView(str) {
    var b = unb64(str);
    return new DataView(b.buffer, b.byteOffset, b.byteLength);
  }

  // ---- USBDevice ----

  function makeConfig(c) {
    var byNum = {};
    var order = [];
    (c.interfaces || []).forEach(function (a) {
      var alt = {
        alternateSetting: a.alternateSetting,
        interfaceClass: a.interfaceClass,
        interfaceSubclass: a.interfaceSubclass,
        interfaceProtocol: a.interfaceProtocol,
        interfaceName: null,
        endpoints: (a.endpoints || []).map(function (e) {
          return { endpointNumber: e.endpointNumber, direction: e.direction, type: e.type, packetSize: e.packetSize };
        })
      };
      var itf = byNum[a.interfaceNumber];
      if (!itf) {
        itf = byNum[a.interfaceNumber] = { interfaceNumber: a.interfaceNumber, alternate: null, alternates: [], claimed: false };
        order.push(itf);
      }
      itf.alternates.push(alt);
      if (a.alternateSetting === 0 || !itf.alternate) itf.alternate = alt;
    });
    return { configurationValue: c.configurationValue, configurationName: null, interfaces: order };
  }

  function USBDevice(d) {
    this._id = d.id;
    this.vendorId = d.vendorId;
    this.productId = d.productId;
    this.deviceClass = d.deviceClass;
    this.deviceSubclass = d.deviceSubclass;
    this.deviceProtocol = d.deviceProtocol;
    this.productName = d.productName;
    this.manufacturerName = d.manufacturerName;
    this.serialNumber = d.serialNumber;
    this.usbVersionMajor = 2;
    this.usbVersionMinor = 0;
    this.usbVersionSubminor = 0;
    this.deviceVersionMajor = 0;
    this.deviceVersionMinor = 0;
    this.deviceVersionSubminor = 0;
    this.configurations = (d.configurations || []).map(makeConfig);
    this.configuration = null;
    this.opened = false;
  }

  function iface(dev, n) {
    var cfg = dev.configuration;
    if (!cfg) return null;
    for (var i = 0; i < cfg.interfaces.length; i++) if (cfg.interfaces[i].interfaceNumber === n) return cfg.interfaces[i];
    return null;
  }

  function requestTypeByte(setup, dirIn) {
    var t = { standard: 0x00, 'class': 0x20, vendor: 0x40 }[setup.requestType];
    var r = { device: 0x00, 'interface': 0x01, endpoint: 0x02, other: 0x03 }[setup.recipient];
    if (t === undefined || r === undefined) throw new TypeError('bad USB control setup');
    return (dirIn ? 0x80 : 0) | t | r;
  }

  USBDevice.prototype.open = function () {
    var self = this;
    return call('usbOpen', [this._id]).then(function () {
      self.opened = true;
      if (!self.configuration) self.configuration = self.configurations[0] || null;
    });
  };
  USBDevice.prototype.close = function () {
    var self = this;
    return call('usbClose', [this._id]).then(function () {
      self.opened = false;
      if (self.configuration) self.configuration.interfaces.forEach(function (i) { i.claimed = false; });
    });
  };
  USBDevice.prototype.forget = function () { return this.close(); };
  USBDevice.prototype.selectConfiguration = function (value) {
    var self = this;
    return call('usbSelectConfiguration', [this._id, value]).then(function () {
      for (var i = 0; i < self.configurations.length; i++) {
        if (self.configurations[i].configurationValue === value) self.configuration = self.configurations[i];
      }
    });
  };
  USBDevice.prototype.claimInterface = function (n) {
    var self = this;
    return call('usbClaimInterface', [this._id, n]).then(function () { var i = iface(self, n); if (i) i.claimed = true; });
  };
  USBDevice.prototype.releaseInterface = function (n) {
    var self = this;
    return call('usbReleaseInterface', [this._id, n]).then(function () { var i = iface(self, n); if (i) i.claimed = false; });
  };
  USBDevice.prototype.selectAlternateInterface = function () { return Promise.resolve(); };
  USBDevice.prototype.reset = function () { return Promise.resolve(); };
  USBDevice.prototype.controlTransferOut = function (setup, data) {
    var bytes = u8(data);
    return call('usbControl', [this._id, requestTypeByte(setup, false), setup.request, setup.value, setup.index,
      bytes.length ? b64(bytes) : null, bytes.length]).then(function (r) {
      return { status: r.status, bytesWritten: r.bytesWritten || 0 };
    });
  };
  USBDevice.prototype.controlTransferIn = function (setup, length) {
    return call('usbControl', [this._id, requestTypeByte(setup, true), setup.request, setup.value, setup.index, null, length])
      .then(function (r) { return { status: r.status, data: r.status === 'ok' ? dataView(r.data) : undefined }; });
  };
  USBDevice.prototype.transferIn = function (endpoint, length) {
    return call('usbTransferIn', [this._id, endpoint, length]).then(function (r) {
      return { status: r.status, data: dataView(r.data) };
    });
  };
  USBDevice.prototype.transferOut = function (endpoint, data) {
    return call('usbTransferOut', [this._id, endpoint, b64(u8(data))]).then(function (r) {
      return { status: r.status, bytesWritten: r.bytesWritten };
    });
  };
  USBDevice.prototype.clearHalt = function (direction, endpoint) {
    return call('usbClearHalt', [this._id, direction === 'in', endpoint]);
  };

  // One object per plugged-in device (the page keys maps by it).
  var devices = {};
  function deviceFor(d) {
    var dev = devices[d.id];
    if (!dev) dev = devices[d.id] = new USBDevice(d);
    else if (d.serialNumber && !dev.serialNumber) dev.serialNumber = d.serialNumber;
    return dev;
  }

  // ---- navigator.usb ----

  var usb = new EventTarget();
  usb.onconnect = null;
  usb.ondisconnect = null;
  usb.getDevices = function () {
    return call('usbGetDevices', []).then(function (list) { return list.map(deviceFor); });
  };
  usb.requestDevice = function (opts) {
    var filters = (opts && opts.filters) || [];
    return call('usbRequestDevice', [JSON.stringify(filters)]).then(function (d) {
      if (!d) throw domError('NotFoundError', 'No device selected.');
      return deviceFor(d);
    });
  };
  Object.defineProperty(navigator, 'usb', { value: usb, configurable: true, enumerable: true });

  window.__hondalogUsbEvent = function (type, json) {
    var d = JSON.parse(json);
    var dev = deviceFor(d);
    if (type === 'disconnect') {
      dev.opened = false;
      delete devices[d.id];
    }
    var ev = new Event(type);
    ev.device = dev;
    usb.dispatchEvent(ev);
    var h = usb['on' + type];
    if (typeof h === 'function') { try { h.call(usb, ev); } catch (e) { console.error(e); } }
  };

  // ---- touch screen ----
  // A car radio: no grey tap flash, no long-press text selection or callout,
  // no overscroll bounce. Text fields stay selectable.
  try {
    var css = document.createElement('style');
    css.textContent = 'html,body{-webkit-tap-highlight-color:transparent;-webkit-touch-callout:none;' +
      '-webkit-user-select:none;user-select:none;overscroll-behavior:none}' +
      'input,textarea,select,[contenteditable],[contenteditable] *{-webkit-user-select:text;user-select:text}';
    (document.head || document.documentElement).appendChild(css);
  } catch (e) { /* cosmetic */ }

  // ---- downloads ----

  function save(name, mime, bytes) {
    var t = B.exportBegin(name, mime || '');
    if (t.indexOf(ERR) === 0) throw new Error(t.slice(1));
    var CH = 768 * 1024;
    for (var i = 0; i < bytes.length; i += CH) {
      var r = B.exportChunk(t, b64(bytes.subarray(i, i + CH)));
      if (r.indexOf(ERR) === 0) throw new Error(r.slice(1));
    }
    var where = B.exportCommit(t);
    if (where.indexOf(ERR) === 0) throw new Error(where.slice(1));
    return where;
  }

  var click = HTMLAnchorElement.prototype.click;
  HTMLAnchorElement.prototype.click = function () {
    var href = this.href || '';
    if (this.hasAttribute('download') && (href.indexOf('blob:') === 0 || href.indexOf('data:') === 0)) {
      var name = this.getAttribute('download') || 'download';
      fetch(href).then(function (r) { return r.blob(); }).then(function (blob) {
        return blob.arrayBuffer().then(function (buf) { save(name, blob.type, new Uint8Array(buf)); });
      }).catch(function (e) { console.error('hondalog: export failed', e); });
      return;
    }
    return click.apply(this, arguments);
  };
})();
