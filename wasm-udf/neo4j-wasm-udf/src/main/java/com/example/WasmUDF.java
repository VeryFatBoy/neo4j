package com.example;

import io.github.kawamuray.wasmtime.Engine;
import io.github.kawamuray.wasmtime.Func;
import io.github.kawamuray.wasmtime.Instance;
import io.github.kawamuray.wasmtime.Module;
import io.github.kawamuray.wasmtime.Store;
import io.github.kawamuray.wasmtime.WasmFunctions;
import io.github.kawamuray.wasmtime.WasmValType;
import org.neo4j.procedure.Description;
import org.neo4j.procedure.Name;
import org.neo4j.procedure.UserFunction;

import java.io.InputStream;
import java.util.Collections;

public class WasmUDF {

    @UserFunction("com.example.wasm.add")
    @Description("Adds two integers using a Wasm module executed via wasmtime-java.")
    public Long add(@Name("a") Long a, @Name("b") Long b) throws Exception {

        if (a == null || b == null) return null;

        byte[] wasmBytes;
        try (InputStream is = WasmUDF.class.getResourceAsStream("/add.wasm")) {
            if (is == null) throw new RuntimeException("add.wasm not found in resources");
            wasmBytes = is.readAllBytes();
        }

        try (Store<Void> store = Store.withoutData();
             Engine engine = store.engine();
             Module module = Module.fromBinary(engine, wasmBytes);
             Instance instance = new Instance(store, module, Collections.emptyList())) {

            try (Func addFn = instance.getFunc(store, "add").get()) {
                WasmFunctions.Function2<Integer, Integer, Integer> add =
                    WasmFunctions.func(store, addFn,
                        WasmValType.I32, WasmValType.I32,
                        WasmValType.I32);
                return (long) add.call(a.intValue(), b.intValue());
            }
        }
    }
}
