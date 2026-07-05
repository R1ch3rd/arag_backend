# Richard Samuel — AI Engineer

Richard Samuel is an AI Engineer based in Bangalore, India. He works at MeshDefend,
an AI-powered IT services company for data infrastructure, where he is currently
full-time after a six-month internship from December to May. He builds agentic AI
systems and production ML infrastructure.

Education: B.E. in Computer Science with an AI & ML specialization from PSG College
of Technology.

Contact: richysamdom@gmail.com
GitHub: github.com/R1ch3rd
LinkedIn: linkedin.com/in/richard-samuel-d
Portfolio: r1ch3rd.github.io/folio

## Research and Publications

### Enhanced Multi-Scale Deep Image Prior for Unsupervised Remote Sensing Image Restoration
Status: Under review at IEEE Geoscience and Remote Sensing Letters (GRSL),
Manuscript GRSL-01134-2026. Unsupervised restoration of satellite imagery using a
multi-scale deep image prior, removing the need for paired clean/corrupted training
data. Developed during a research internship at ISRO/NRSC (Indian Space Research
Organisation / National Remote Sensing Centre).

### FedHyperGNN: A Federated Hypergraph Neural Network for Privacy-Preserving Recommendations
Accepted at IEEE NMITCON 2026. A federated learning approach using hypergraph neural
networks to model higher-order user-item relationships in recommendation systems,
without centralizing user data. Includes differential privacy guarantees with a
configurable privacy budget.

### Reinforcement Learning-Based Adversarial Defense for Medical Imaging
Won Best Paper, Scopus-indexed, at PSG College of Technology. A PPO-based
reinforcement learning framework for defending medical imaging classifiers against
adversarial perturbations. The RL agent selects image preprocessing and denoising
actions at inference time to restore classifier accuracy on perturbed fetal brain
ultrasound images, defending an EfficientNet-B0 classifier against attacks
including PGD, BIM, R+FGSM, DeepFool, and Carlini-Wagner.

### ICCTSD Conference Publication
Published at the International Conference on Computational Techniques in Science
and Defense.

## Projects

### aRAG (this system)
A serverless retrieval-augmented generation platform. Users upload documents and
converse with them. Architecture: AWS Lambda functions behind API Gateway, Cognito
authentication, Pinecone vector search with Gemini embeddings, DynamoDB for
sessions/messages/documents metadata, S3 for document storage, and Upstash Redis
for caching and rate limiting. The chat you are using right now runs on aRAG's
public guest mode.

### FinAL
AI-powered stock analysis platform: LSTM price forecasting in PyTorch, BERT
sentiment analysis on financial news, and Gemini-driven insights layered over live
market data from Finnhub and yfinance. FastAPI backend, React frontend.

### PixelPerfect
AI image transformation suite built at a hackathon: ESRGAN super-resolution
upscaling, Stable Diffusion image generation, and Gemini-powered captioning and
summaries. FastAPI backend, React frontend, Firebase storage.

### SureScan
Brain tumor detection and diagnosis assistant: YOLOv11 localization plus a
classifier ensemble with XGBoost, and an AI chat interface for interpreting scan
results. React frontend.

### Misinformation Detection Agent (ReZero)
Agentic fact-checking service: detects AI-generated text and images with
transformer models, then verifies claims with an LLM agent over the Tavily search
API. FastAPI backend.

## Interests outside work

Tennis (his portfolio hides a playable tennis-pong game), video games (usually
mid-way through something on the PS5), LEGO, and robotics. The long-term obsession
is agents with bodies.

## Frequently asked

What is Richard looking for? Conversations about agentic AI systems, applied ML,
and research collaboration. Reach out at richysamdom@gmail.com.

What stack does he work with? Python, PyTorch, LangGraph, MCP servers, AWS
serverless (Lambda, DynamoDB, Cognito, API Gateway), FastAPI, React/TypeScript,
Pinecone, and the Gemini API, among others.
