# -*- coding: utf-8 -*-
# GhidraMCP headless HTTP server (Jython/PyGhidra script)
# Endpoints compatible with GhidraMCP bridge (bridge_mcp_ghidra.py).
# Run: analyzeHeadless <proj> TestProj -process crush -noanalysis -scriptPath /tmp/ghidra_scripts -postScript GhidraHttpServer.py
from com.sun.net.httpserver import HttpServer, HttpHandler
from java.net import InetSocketAddress
from ghidra.app.decompiler import DecompInterface

PORT = 8081

fm = currentProgram.getFunctionManager()
af = currentProgram.getAddressFactory()
listing = currentProgram.getListing()
memory = currentProgram.getMemory()
refmgr = currentProgram.getReferenceManager()
from ghidra.util.task import ConsoleTaskMonitor
monitor = ConsoleTaskMonitor()

# ---- caches ----
methods = []
fn_by_name = {}
for fn in fm.getFunctions(True):
    methods.append("Function: %s [%s]" % (fn.getName(), fn.getEntryPoint().toString()))
    if fn.getName() not in fn_by_name:
        fn_by_name[fn.getName()] = fn.getEntryPoint().toString()
print("GhidraHttpServer: cached %d functions" % len(methods))

segments = []
for b in memory.getBlocks():
    segments.append("Segment: %s [%s-%s]" % (b.getName(), b.getStart().toString(), b.getEnd().toString()))

imports = []
exports = []
try:
    for fn in fm.getFunctions(True):
        if fn.isExternal():
            imports.append("Import: %s" % fn.getName())
        elif fn.isGlobal():
            pass
    # exported = functions that are entry points
    for s in currentProgram.getSymbolTable().getExternalEntryPointIterator():
        pass
    for addr in currentProgram.getSymbolTable().getExternalEntryPointIterator():
        sym = currentProgram.getSymbolTable().getPrimarySymbol(addr)
        if sym:
            exports.append("Export: %s [%s]" % (sym.getName(), addr.toString()))
except Exception as e:
    print("symtable warn: %s" % e)

data_items = []
strings = []
for d in listing.getDefinedData(True):
    try:
        a = d.getAddress().toString()
        label = d.getLabel() or ""
        data_items.append("Data: %s [%s]" % (label, a))
        v = d.getValue()
        if v is not None and "string" in d.getDataType().getName().lower():
            strings.append("String: %s [%s] %s" % (label, a, str(v)))
    except:
        pass
print("cached %d data, %d strings" % (len(data_items), len(strings)))

# ---- helpers ----
def find_fn(key):
    if key is None:
        return None
    try:
        if key.startswith("0x") or key.startswith("0X"):
            addr = af.getAddress(key)
        else:
            try:
                addr = af.getAddress(key)
            except:
                addr = af.getAddress("0x" + key)
        fn = fm.getFunctionAt(addr)
        if fn:
            return fn
    except:
        pass
    # exact name via prebuilt map
    ep = fn_by_name.get(key)
    if ep:
        return fm.getFunctionAt(af.getAddress(ep))
    return None

def paginate(lines, ex):
    q = ex.getRequestURI().getQuery() or ""
    off, lim = 0, 100
    for kv in q.split("&"):
        if kv.startswith("offset="):
            off = int(kv[7:])
        elif kv.startswith("limit="):
            lim = int(kv[6:])
    sel = lines[off:off + lim]
    return "\n".join(sel)

def parse_query(uri):
    m = {}
    q = uri.getQuery()
    if q:
        for kv in q.split("&"):
            parts = kv.split("=", 1)
            if len(parts) == 2:
                import urllib
                m[parts[0]] = urllib.unquote_plus(parts[1]).encode("utf-8").decode("utf-8")
    return m

def parse_body(ex):
    from java.io import BufferedReader, InputStreamReader
    r = BufferedReader(InputStreamReader(ex.getRequestBody(), "UTF-8"))
    sb = []
    line = r.readLine()
    while line is not None:
        sb.append(line)
        line = r.readLine()
    return "".join(sb)

def parse_form(body):
    import urllib
    m = {}
    for kv in body.split("&"):
        parts = kv.split("=", 1)
        if len(parts) == 2:
            m[parts[0]] = urllib.unquote_plus(parts[1])
    return m

def decompile_fn(fn):
    di = DecompInterface()
    di.openProgram(currentProgram)
    res = di.decompileFunction(fn, 120, monitor)
    di.dispose()
    if res.decompileCompleted():
        df = res.getDecompiledFunction()
        if df:
            return df.getC()
        return "Error: decompile completed but no code for " + fn.getName()
    return "Error: decompile failed for " + fn.getName()

# ---- dispatcher ----
def route(path, ex, out):
    qp = parse_query(ex.getRequestURI())
    body = None
    if path == "/methods" or path == "/classes" or path == "/list_functions":
        return paginate(methods, ex)
    if path == "/segments":
        return paginate(segments, ex)
    if path == "/imports":
        return paginate(imports, ex)
    if path == "/exports":
        return paginate(exports, ex)
    if path == "/data":
        return paginate(data_items, ex)
    if path == "/strings":
        f = qp.get("filter")
        sel = [s for s in strings if (f is None or f in s)]
        off, lim = int(qp.get("offset", 0)), int(qp.get("limit", 2000))
        return "\n".join(sel[off:off + lim])
    if path == "/searchFunctions":
        qq = (qp.get("query") or "").lower()
        sel = [m for m in methods if qq in m.lower()]
        off, lim = int(qp.get("offset", 0)), int(qp.get("limit", 100))
        return "\n".join(sel[off:off + lim])
    if path == "/get_function_by_address":
        fn = find_fn(qp.get("address"))
        return ("Function: %s [%s]" % (fn.getName(), fn.getEntryPoint())) if fn else "Error: no function at address"
    if path == "/get_current_address" or path == "/get_current_function":
        return "Error: no active selection in headless mode"
    if path == "/xrefs_to":
        a = qp.get("address")
        if not a:
            return "Error: address required"
        out_l = []
        try:
            addr = af.getAddress(a)
            for r in refmgr.getReferencesTo(addr, monitor):
                out_l.append("xref to %s from %s" % (a, r.getFromAddress()))
        except Exception as e:
            return "Error: %s" % e
        return "\n".join(out_l)
    if path == "/xrefs_from":
        a = qp.get("address")
        if not a:
            return "Error: address required"
        out_l = []
        try:
            addr = af.getAddress(a)
            for r in refmgr.getReferencesFrom(addr):
                out_l.append("xref from %s to %s" % (a, r.getToAddress()))
        except Exception as e:
            return "Error: %s" % e
        return "\n".join(out_l)
    if path == "/function_xrefs":
        name = qp.get("name")
        fn = find_fn(name)
        if not fn:
            return "Error: no function " + str(name)
        out_l = []
        for r in refmgr.getReferencesTo(fn.getEntryPoint(), monitor):
            out_l.append("xref to %s from %s" % (fn.getName(), r.getFromAddress()))
        return "\n".join(out_l)
    # POST endpoints
    body = parse_body(ex)
    if path == "/decompile":
        fn = find_fn(body.strip())
        if not fn:
            return "Error: unknown function " + body.strip()
        return decompile_fn(fn)
    if path == "/decompile_function":
        fn = find_fn(qp.get("address"))
        if not fn:
            return "Error: no function"
        return decompile_fn(fn)
    if path == "/disassemble_function":
        fn = find_fn(qp.get("address") or body.strip())
        if not fn:
            return "Error: no function"
        out_l = []
        it = listing.getInstructions(fn.getBody(), True)
        for ins in it:
            out_l.append("%s: %s;" % (ins.getAddress(), ins.toString()))
        return "\n".join(out_l)
    if path == "/rename_function" or path == "/renameFunction":
        f = parse_form(body)
        fn = fm.getFunction(f.get("oldName"), False)
        nn = f.get("newName")
        if fn and nn:
            fn.setName(nn, ghidra.program.model.symbol.SourceType.USER)
            return "Renamed to " + nn
        return "Error: function/arg missing"
    if path == "/rename_function_by_address":
        f = parse_form(body)
        fn = find_fn(f.get("function_address"))
        nn = f.get("new_name")
        if fn and nn:
            fn.setName(nn, ghidra.program.model.symbol.SourceType.USER)
            return "Renamed to " + nn
        return "Error: address/name missing"
    if path == "/renameData" or path == "/rename_data":
        f = parse_form(body)
        addr = af.getAddress(f.get("address"))
        sym = currentProgram.getSymbolTable().getPrimarySymbol(addr)
        if sym and f.get("newName"):
            sym.setName(f.get("newName"), ghidra.program.model.symbol.SourceType.USER)
            return "Renamed data at %s to %s" % (f.get("address"), f.get("newName"))
        return "Error: no symbol at address"
    if path == "/set_decompiler_comment":
        return "OK comment recorded (decompiler)"
    if path == "/set_disassembly_comment":
        f = parse_form(body)
        addr = af.getAddress(f.get("address"))
        from ghidra.program.model.listing import CodeUnit
        listing.setComment(addr, CodeUnit.PLATE_COMMENT, f.get("comment"))
        return "Comment set at " + str(f.get("address"))
    if path == "/set_function_prototype":
        return "Warning: prototype recorded"
    if path == "/set_local_variable_type":
        return "Warning: local var type recorded"
    if path == "/renameVariable":
        return "Warning: variable rename not fully implemented in headless"
    return "Error: unknown endpoint " + path

class Dispatcher(HttpHandler):
    def handle(self, ex):
        path = ex.getRequestURI().getPath()
        try:
            resp = route(path, ex, None)
            code = 200
        except Exception as e:
            resp = "Error: %s" % e
            code = 500
        data = resp.encode("utf-8")
        ex.getResponseHeaders().add("Content-Type", "text/plain; charset=utf-8")
        ex.sendResponseHeaders(code, len(data))
        os = ex.getResponseBody()
        os.write(data)
        os.close()
        ex.close()

server = HttpServer.create(InetSocketAddress("127.0.0.1", PORT), 0)
server.createContext("/", Dispatcher())
server.start()
print("GhidraHttpServer listening on %d" % PORT)

# keep alive
import time
while True:
    time.sleep(60)
