import logging #logs
from fastapi import FastAPI 
import inngest
import inngest.fast_api
from dotenv import load_dotenv #key
import uuid #chat ids every chunk has a unique id
import os #os
import datetime
from inngest.experimental import ai
from data_loader import load_and_chunk_pdf, embed_texts
from vector_db import QdrantStorage
from custom_type import RAGSearchResult, RAGUpsertResult, RAGChunckAndSrc, RAQQueryResult
##inngest 
load_dotenv() #load env variables 
# connection to inngest
inngest_client = inngest.Inngest(
    app_id="rag_app", #name
    logger=logging.getLogger("uvicorn"), #logs
    is_production=False, 
    serializer=inngest.PydanticSerializer() #typing support for events and payloads

) 
# PHASE 1: loading the files and chunks and embedding them into vector db

#inngest function take then send to api
@inngest_client.create_function( #decorator #fnct called by inngest if event is triggered
    fn_id="RAG : Ingest PDF",
    trigger=inngest.TriggerEvent(event="rag/ingest_pdf")
)
async def rag_ingest_pdf(ctx: inngest.Context):
    def _load(ctx: inngest.Context):
        pdf_path = ctx.event.data["pdf_path"]
        source_id = ctx.event.data.get("source_id", pdf_path)

        chunks = load_and_chunk_pdf(pdf_path)

        return {
            "chunks": chunks,
            "source_id": source_id,
        }
    
    
    def _upsert(chunks_and_src) -> RAGUpsertResult:
        if isinstance(chunks_and_src, dict):
            chunks_and_src = RAGChunckAndSrc(**chunks_and_src)

        chunks = chunks_and_src.chunks
        source_id = chunks_and_src.source_id

        vecs = embed_texts(chunks)

        ids = [
            str(uuid.uuid5(uuid.NAMESPACE_URL, f"{source_id}:{i}"))
            for i in range(len(chunks))
        ]

        payloads = [
            {"source": source_id, "text": chunks[i]}
            for i in range(len(chunks))
        ]

        #QdrantStorage().upsert(ids, vecs, payloads) ##insert/ update into vector db
        #return { 
        #    "ingested": len(chunks),
        #}

        store = QdrantStorage()
        store.delete_source(source_id)        # remove all old chunks of this file
        store.upsert(ids, vecs, payloads)     # write the new ones
        return {"ingested": len(chunks)
        }
    
    chunks_and_src = await ctx.step.run(
        "load-and-chunck",
        lambda: _load(ctx)
    )
    ingested = await ctx.step.run(
        "embed-and-upsert",
        lambda: _upsert(chunks_and_src)
    )

    return ingested

# PHASE 2: retrieve and answer question based on context
@inngest_client.create_function(
    fn_id="RAG : Query",
    trigger=inngest.TriggerEvent(event="rag/query_pdf_ai"),
)
async def rag_query_pdf_ai(ctx: inngest.Context):

    question = ctx.event.data["question"]
    top_k = ctx.event.data.get("top_k", 5)

    def _search(question: str, top_k: int = 5):
        query_vec = embed_texts([question])[0] #first and only element of the list, embedding for the question
        store = QdrantStorage()
        found = store.search(query_vec, top_k)

        return {
            "contexts": found["contexts"],
            "sources": found["sources"],
        }

    found = await ctx.step.run( #await : pause the function until the result is returned
        "search",
        lambda: _search(question, top_k),
    )

    if isinstance(found, dict):
        found = RAGSearchResult(**found)
#gpt gets involed!
    context_block = "\n\n".join(f"- {c}" for c in found.contexts)

    user_content = (
        "Answer the question based on the context below. "
        "If the context does not contain the answer, say 'I don't know'.\n\n"
        f"Context:\n{context_block}\n\n"
        f"Question: {question}\n\n"
        "Answer:"
    )
#knows how to speak to gpt, send the question and context to gpt and get the answer
    adapter = ai.openai.Adapter(
    auth_key=os.environ.get("OPENAI_API_KEY"),
    model="gpt-4o",
)

    res = await ctx.step.ai.infer(
        "answer-question",
        adapter=adapter,
        body={
            "max_tokens": 500,
            "temperature": 0.2,
            "messages": [
                {#you are a teacher/be firm/dont use emojis, etc.
                    "role": "system",
                    "content": "You are a helpful assistant.",
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
        },
    )

    answer = res["choices"][0]["message"]["content"]
    #sent back to inngest to be returned to the app
    return {
        "answer": answer,
        "sources": found.sources,
        "num_contexts": len(found.contexts),
    }

##await ctx?

app = FastAPI() 
# inngest connects to fastapi app, and run the functions defined below 
inngest.fast_api.serve(
    app,
    inngest_client,
    functions=[rag_ingest_pdf,rag_query_pdf_ai]
)