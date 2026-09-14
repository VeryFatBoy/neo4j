package com.example;

import io.github.kawamuray.wasmtime.Engine;
import io.github.kawamuray.wasmtime.Func;
import io.github.kawamuray.wasmtime.Linker;
import io.github.kawamuray.wasmtime.Memory;
import io.github.kawamuray.wasmtime.Module;
import io.github.kawamuray.wasmtime.Store;
import io.github.kawamuray.wasmtime.WasmFunctions;
import io.github.kawamuray.wasmtime.WasmValType;
import io.github.kawamuray.wasmtime.wasi.WasiCtx;
import io.github.kawamuray.wasmtime.wasi.WasiCtxBuilder;
import org.neo4j.procedure.Description;
import org.neo4j.procedure.Name;
import org.neo4j.procedure.UserFunction;

import java.io.InputStream;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.util.HashMap;
import java.util.Map;

public class SentimentUDF {

    @UserFunction("com.example.wasm.sentiment")
    @Description("Scores text using VADER sentiment analysis compiled to Wasm. Returns compound, positive, negative, neutral.")
    public Map<String, Double> sentiment(@Name("text") String text) throws Exception {

        if (text == null || text.isBlank()) {
            return Map.of("compound", 0.0, "positive", 0.0, "negative", 0.0, "neutral", 1.0);
        }

        byte[] wasmBytes;
        try (InputStream is = SentimentUDF.class.getResourceAsStream("/sentimentable.wasm")) {
            if (is == null) throw new RuntimeException("sentimentable.wasm not found in resources");
            wasmBytes = is.readAllBytes();
        }

        WasiCtx wasi = new WasiCtxBuilder().inheritStdout().inheritStderr().build();

        try (Store<Void> store = Store.withoutData(wasi);
             Engine engine = store.engine();
             Module module = Module.fromBinary(engine, wasmBytes);
             Linker linker = new Linker(engine)) {

            WasiCtx.addToLinker(linker);
            linker.module(store, "", module);

            Memory memory = linker.get(store, "", "memory").get().memory();

            Func reallocFn = linker.get(store, "", "cabi_realloc").get().func();
            WasmFunctions.Function4<Integer, Integer, Integer, Integer, Integer> realloc =
                WasmFunctions.func(store, reallocFn,
                    WasmValType.I32, WasmValType.I32, WasmValType.I32, WasmValType.I32,
                    WasmValType.I32);

            byte[] inputBytes = text.getBytes(StandardCharsets.UTF_8);
            int len = inputBytes.length;
            int strPtr = realloc.call(0, 0, 1, len);

            ByteBuffer buf = memory.buffer(store);
            buf.position(strPtr);
            buf.put(inputBytes);

            Func sentimentFn = linker.get(store, "", "sentimentable").get().func();
            WasmFunctions.Function2<Integer, Integer, Integer> scoreFn =
                WasmFunctions.func(store, sentimentFn,
                    WasmValType.I32, WasmValType.I32,
                    WasmValType.I32);

            int resultPtr = scoreFn.call(strPtr, len);

            ByteBuffer resultBuf = memory.buffer(store);
            resultBuf.order(ByteOrder.LITTLE_ENDIAN);
            float compound = resultBuf.getFloat(resultPtr);
            float positive = resultBuf.getFloat(resultPtr + 4);
            float negative = resultBuf.getFloat(resultPtr + 8);
            float neutral  = resultBuf.getFloat(resultPtr + 12);

            Map<String, Double> result = new HashMap<>();
            result.put("compound", (double) compound);
            result.put("positive", (double) positive);
            result.put("negative", (double) negative);
            result.put("neutral",  (double) neutral);
            return result;
        }
    }
}
