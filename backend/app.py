"""
FastAPI backend for LLM visualization

Provides REST API endpoints for GPT-2 model analysis including:
- Text tokenization
- Embedding extraction
- Attention weight computation
- Next token prediction

Uses transformers library with GPT-2 model and sklearn for PCA.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware  # ✅ CORS
from pydantic import BaseModel
from transformers import GPT2LMHeadModel, GPT2Tokenizer
import torch
from sklearn.decomposition import PCA
import numpy as np


app = FastAPI(title="LLM Visualization API", version="1.0.0")

# ✅ Allow frontend (React @ localhost:5173) to make API calls
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Or use ["http://localhost:5173"] for stricter setup
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Load tokenizer and model (GPT-2)
try:
    tokenizer = GPT2Tokenizer.from_pretrained("gpt2")
    model = GPT2LMHeadModel.from_pretrained("gpt2", output_hidden_states=True, output_attentions=True)
    model.eval()
    # GPT-2 tokenizer doesn't have a pad token by default
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
except Exception as e:
    print("Model/tokenizer load error:", e)
    tokenizer = None
    model = None


class TextInput(BaseModel):
    """Request model for text input endpoints"""
    text: str


@app.post("/tokenize")
def tokenize_text(input: TextInput):
    """
    Tokenize input text using GPT-2 tokenizer
    
    Args:
        input: TextInput object containing text to tokenize
        
    Returns:
        dict: Contains input_ids, cleaned tokens, and attention_mask
    """
    if tokenizer is None:
        print("/tokenize error: tokenizer not loaded")
        return {"input_ids": [], "tokens": [], "attention_mask": []}
    try:
        inputs = tokenizer(input.text, return_tensors="pt", add_special_tokens=True)
        raw_tokens = tokenizer.convert_ids_to_tokens(inputs["input_ids"][0])
        input_ids = inputs["input_ids"][0].tolist()
        
        # Clean up tokens for better display and remove duplicates
        cleaned_tokens = []
        seen_tokens = set()
        
        for token in raw_tokens:
            # Clean the token
            if token.startswith('Ġ'):
                clean_token = ' ' + token[1:]
            else:
                clean_token = token
            
            # Only add if we haven't seen this exact token before
            if clean_token not in seen_tokens:
                cleaned_tokens.append(clean_token)
                seen_tokens.add(clean_token)
        
        return {
            "input_ids": input_ids,
            "tokens": cleaned_tokens,
            "attention_mask": inputs["attention_mask"].tolist()
        }
    except Exception as e:
        print("/tokenize error:", e)
        return {"input_ids": [], "tokens": [], "attention_mask": []}

@app.post("/next_token")
def next_token_prediction(input: TextInput):
    """
    Predict next token using GPT-2 model
    
    Args:
        input: TextInput object containing text for prediction
        
    Returns:
        dict: Contains predicted token, token_id, probability, and top 10 probabilities
    """
    if tokenizer is None or model is None:
        print("/next_token error: model/tokenizer not loaded")
        return {"token": "", "token_id": -1, "probability": 0.0, "probs": []}
    try:
        # Tokenize input text
        inputs = tokenizer.encode(input.text, return_tensors="pt")
        
        with torch.no_grad():
            # Get model outputs
            outputs = model(inputs)
            logits = outputs.logits
            
            # Get the logits for the last token (next token prediction)
            next_token_logits = logits[0, -1, :]
            
            # Apply softmax to get probabilities
            probs = torch.softmax(next_token_logits, dim=-1)
            
            # Get top 10 probabilities and their corresponding tokens
            top_probs, top_indices = torch.topk(probs, k=10, dim=-1)
            
            # Convert to list and get tokens for each probability
            top_probs_list = top_probs.tolist()
            top_tokens = [tokenizer.decode([idx.item()]) for idx in top_indices]
            
            # Get raw logits for the top 10 tokens (for softmax animation)
            top_logits = next_token_logits[top_indices].tolist()
            
            # Format probabilities for frontend
            probs_list = [{"token": token.strip(), "prob": prob, "logit": logit} for token, prob, logit in zip(top_tokens, top_probs_list, top_logits)]
            
            # Get the top token (highest probability)
            token = top_tokens[0].strip()
            token_id = top_indices[0].item()
            probability = top_probs_list[0]
            
        return {
            "token": token,
            "token_id": token_id,
            "probability": probability,
            "probs": probs_list
        }
    except Exception as e:
        print("/next_token error:", e)
        return {"token": "", "token_id": -1, "probability": 0.0, "probs": []}


@app.post("/residual_stream")
def get_residual_stream(input: TextInput):
    """
    Compute residual stream evolution (e.g., norm or PCA dimension) across GPT-2 layers.
    
    Returns:
        dict: Each token gets an array of norm or PCA(1D) per layer
    """
    if tokenizer is None or model is None:
        return {"layer_values": []}
    try:
        inputs = tokenizer(input.text, return_tensors="pt", add_special_tokens=True)
        with torch.no_grad():
            outputs = model(**inputs, output_hidden_states=True)
        hidden_states = outputs.hidden_states  # List of tensors: [layer, 1, seq_len, hidden_dim]

        # Convert each layer to [seq_len, hidden_dim] and calculate norm per token
        token_layer_values = []  # List of token trajectories (each is [layer_0_val, layer_1_val, ..., layer_n_val])
        num_tokens = hidden_states[0].shape[1]
        num_layers = len(hidden_states)

        for token_idx in range(num_tokens):
            values_per_layer = []
            for layer_idx in range(num_layers):
                vec = hidden_states[layer_idx][0, token_idx]  # Shape: [hidden_dim]
                values_per_layer.append(torch.norm(vec).item())  # Could also use vec[0].item() for PCA dim
            token_layer_values.append(values_per_layer)

        return {
            "layer_values": token_layer_values,
            "tokens": tokenizer.convert_ids_to_tokens(inputs["input_ids"][0]),
            "num_layers": num_layers
        }
    except Exception as e:
        print("Error in /residual_stream:", e)
        return {"layer_values": [], "tokens": [], "num_layers": 0}


@app.post("/attention")
def get_attention(input: TextInput):
    """
    Extract attention weights from GPT-2 model
    
    Args:
        input: TextInput object containing text to analyze
        
    Returns:
        dict: Contains number of layers and attention matrices for all layers/heads
    """
    if tokenizer is None or model is None:
        print("/attention error: model/tokenizer not loaded")
        return {"num_layers": 0, "attentions": []}
    try:
        inputs = tokenizer(input.text, return_tensors="pt", add_special_tokens=True)
        with torch.no_grad():
            outputs = model(**inputs)
        attentions = outputs.attentions if hasattr(outputs, 'attentions') else []
        attentions_data = [layer.squeeze(0).tolist() for layer in attentions] if attentions else []
        return {
            "num_layers": len(attentions_data),
            "attentions": attentions_data
        }
    except Exception as e:
        print("/attention error:", e)
        return {"num_layers": 0, "attentions": []}

@app.post("/embeddings")
def get_embeddings(input: TextInput):
    """
    Extract hidden states and compute 3D embeddings using PCA
    
    Args:
        input: TextInput object containing text to analyze
        
    Returns:
        dict: Contains hidden states from all layers and 3D PCA embeddings
    """
    if tokenizer is None or model is None:
        print("/embeddings error: model/tokenizer not loaded")
        return {"num_layers": 0, "hidden_states": [], "embeddings3d": []}
    try:
        inputs = tokenizer(input.text, return_tensors="pt", add_special_tokens=True)
        with torch.no_grad():
            outputs = model(**inputs)
        hidden_states = outputs.hidden_states if hasattr(outputs, 'hidden_states') else []
        hidden_states_data = [layer.squeeze(0).tolist() for layer in hidden_states] if hidden_states else []
        # embeddings3d: PCA of last hidden state (for visualization)
        embeddings3d = []
        if hidden_states_data and len(hidden_states_data[-1]) > 0:
            try:
                last_hidden = np.array(hidden_states_data[-1])  # [seq_len, hidden_dim]
                if last_hidden.shape[1] >= 3:
                    pca = PCA(n_components=3)
                    embeddings3d = pca.fit_transform(last_hidden).tolist()
            except Exception as e:
                print("PCA error:", e)
                embeddings3d = []
        # Always return all keys, even if empty
        return {
            "num_layers": len(hidden_states_data),
            "hidden_states": hidden_states_data,
            "embeddings3d": embeddings3d if isinstance(embeddings3d, list) else []
        }
    except Exception as e:
        print("/embeddings error:", e)
        return {"num_layers": 0, "hidden_states": [], "embeddings3d": []}

@app.get("/health")
def health_check():
    """Health check endpoint for API status"""
    return {"status": "ok", "model": "gpt2"}
