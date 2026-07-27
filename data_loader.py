#llama index to load pdf and embed them
from openai import OpenAI
from llama_index.readers.file import PDFReader #pdf readers class
from llama_index.core.node_parser import SentenceSplitter
from dotenv import load_dotenv

load_dotenv()

client = OpenAI()
#load pdf , cant embed entire thing,break it to peices
#llama to read pdf and split into sentences
EMBED_MODEL = "text-embedding-3-large" #creates vectors
EMBED_DIM = 3072

splitter=SentenceSplitter(
    chunk_size=1000,
    chunk_overlap=200,
)
def load_and_chunk_pdf(path: str):
    docs = PDFReader().load_data(file=path)#look and load pdf
    texts = [d.text for d in docs if getattr(d,"text",None)]
    chuncks=[]
    for t in texts:
        chuncks.extend(splitter.split_text(t))
    return chuncks

#pass text to openai to get embeddings
def embed_texts(texts:list[str])->list[list[float]]:#returns vectors from str
    response = client.embeddings.create(
        model=EMBED_MODEL,
        input=texts
    )
    return [item.embedding for item in response.data]###
#pdf turned into vectors, now store them in vector db